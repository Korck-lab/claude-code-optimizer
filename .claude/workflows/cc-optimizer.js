// cc-optimizer — LLM refinement layer of the Claude Code quota-waste levantamento.
//
// Reusable, dynamic workflow. It consumes ONLY the compact per-project briefs +
// a compact knowledge base produced by the deterministic Python pipeline (run.sh)
// — it NEVER reads the raw .jsonl transcripts. Per-agent model economy: Haiku to
// load the manifest, Opus only for the top-spending (tier A) projects, Sonnet for
// the rest, one Opus rollup.
//
//   Workflow({ name: "cc-optimizer",
//              args: { refineArgsPath: "<abs>/optimizer/out/refine-args.json" } })
//
// Output { refined, rollup }; feed it to merge.py + report.py for the final report.
export const meta = {
  name: 'cc-optimizer',
  description: 'Refine deterministic per-project Claude Code quota-waste findings into validated, project-specific actionable optimizations. Reads only compact briefs + KB, never raw transcripts. Opus for top spenders, Sonnet for the rest, Haiku to load.',
  phases: [
    { title: 'Load', detail: 'Haiku reads the brief manifest' },
    { title: 'Refine', detail: 'one agent per project (Opus top-5 / Sonnet rest) validates + tightens findings' },
    { title: 'Rollup', detail: 'one Opus agent synthesizes cross-project + global user-level actions' },
  ],
}

const LOAD_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    kb: { type: 'string' },
    projects: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: { project: { type: 'string' }, name: { type: 'string' }, tier: { type: 'string' }, path: { type: 'string' } },
      required: ['project', 'name', 'tier', 'path'],
    } },
  },
  required: ['kb', 'projects'],
}

const REFINE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    profile: { type: 'string' },
    findings: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: {
        signal: { type: 'string' },
        knob: { type: 'string' },
        config_location: { type: 'string' },
        recommended_value: { type: 'string' },
        est_saving_usd: { type: 'number' },
        confidence: { type: 'string' },
        rationale: { type: 'string' },
        keep: { type: 'boolean' },
      },
      required: ['signal', 'config_location', 'recommended_value', 'est_saving_usd', 'confidence', 'keep', 'rationale'],
    } },
  },
  required: ['profile', 'findings'],
}

const ROLLUP_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    executive_summary: { type: 'string' },
    global_actions: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: { action: { type: 'string' }, config_location: { type: 'string' }, applies_to: { type: 'string' }, rationale: { type: 'string' } },
      required: ['action', 'config_location', 'rationale'],
    } },
    cross_project_patterns: { type: 'array', items: { type: 'string' } },
    caveats: { type: 'array', items: { type: 'string' } },
  },
  required: ['executive_summary', 'global_actions'],
}

const refineArgsPath = (args && args.refineArgsPath) || 'optimizer/out/refine-args.json'

phase('Load')
const loaded = await agent(
  'Use the Read tool on the JSON file at ' + refineArgsPath + ' and return its EXACT contents verbatim: an object with keys "kb" (a file path string) and "projects" (an array of {project,name,tier,path}). Copy every path character-for-character; do not alter, shorten, or reorder anything.',
  { label: 'load-manifest', phase: 'Load', agentType: 'general-purpose', model: 'haiku', effort: 'low', schema: LOAD_SCHEMA }
)
const kb = loaded.kb
const projects = loaded.projects
log('loaded ' + projects.length + ' projects to refine')

const REFINE_PROMPT = (pr) =>
  'You are tightening QUOTA-WASTE findings for ONE Claude Code project: "' + pr.name + '". You have ONLY compact aggregate stats — NEVER the raw transcripts. Do not invent numbers.\n\n' +
  'Use the Read tool on these two files:\n' +
  '1. Project brief: ' + pr.path + '\n' +
  '2. Compact knowledge base (actionable config knobs + pricing): ' + kb + '\n\n' +
  'The brief has the project token/$ stats by model, subagent-by-model, MCP servers, cache hit, 1h-cache, base context, and a list of deterministic_findings (each already maps to a real config knob with an estimated $ saving and an evidence string).\n\n' +
  'For EACH deterministic finding decide:\n' +
  '- keep (true/false): keep ONLY if genuinely actionable for THIS project AND safe. Drop if the brief shows it does not really apply.\n' +
  '- recommended_value: concrete + project-appropriate. E.g. subagent model = haiku for read/scan-heavy projects but sonnet if the project clearly writes a lot of code; for prune-MCP name the specific servers in mcp_calls that look unused/heavy; for default model pick sonnet or opusplan.\n' +
  '- est_saving_usd: keep the deterministic value; you may LOWER it if optimistic; NEVER raise it; never invent a larger number.\n' +
  '- confidence: low/medium/high grounded in the evidence.\n' +
  '- rationale: ONE line citing the actual numbers from the brief.\n\n' +
  'You MAY add at most ONE new finding ONLY if the brief clearly shows an actionable waste mapping to a KB knob the deterministic engine missed; be conservative, modest est_saving_usd (0 if not derivable), confidence low.\n\n' +
  'ONLY actionable config changes (settings key, env var, model alias, subagent frontmatter, MCP prune, CLAUDE.md/hook). NO advisory tips. Return JSON per schema; profile = 1-2 line waste profile of this project.'

phase('Refine')
const refined = await parallel(projects.map((pr) => () =>
  agent(REFINE_PROMPT(pr), {
    label: 'refine:' + pr.name, phase: 'Refine', agentType: 'general-purpose',
    model: pr.tier === 'A-strong' ? 'opus' : 'sonnet',
    effort: pr.tier === 'A-strong' ? 'high' : 'medium',
    schema: REFINE_SCHEMA,
  }).then((r) => Object.assign({ project: pr.project, name: pr.name, tier: pr.tier }, r)).catch(() => null)
))
const ok = refined.filter(Boolean)
log('refined ' + ok.length + '/' + projects.length + ' projects')

const compact = ok.map((p) => ({
  name: p.name, tier: p.tier, profile: p.profile,
  kept: (p.findings || []).filter((f) => f.keep).map((f) => ({ signal: f.signal, value: f.recommended_value, usd: f.est_saving_usd, conf: f.confidence })),
}))

phase('Rollup')
const rollup = await agent(
  'You are writing the cross-project rollup of a Claude Code quota-optimization levantamento (survey). Opus tends to dominate token usage; the deterministic engine already estimated per-project savings. Dominant levers: subagents on expensive models, 1h-cache overuse, expensive default model on trivial turns, MCP/context bloat.\n\n' +
  'Refined per-project findings (compact JSON):\n' + JSON.stringify(compact) + '\n\n' +
  'Produce (write prose in Portuguese):\n' +
  '- executive_summary: 3-5 sentences for a decision-maker.\n' +
  '- global_actions: the highest-blast-radius USER-LEVEL config changes (~/.claude/settings.json) that help across many projects (e.g. CLAUDE_CODE_SUBAGENT_MODEL=haiku, effortLevel=medium, MAX_MCP_OUTPUT_TOKENS). Each: action, config_location (exact key/file), applies_to (which projects or "all"), rationale. ACTIONABLE config only — no advisory tips.\n' +
  '- cross_project_patterns: bullet observations.\n' +
  '- caveats: honest caveats (estimates are not a bill; quality trade-offs; this is a levantamento — nothing is applied).\n' +
  'Return JSON per schema.',
  { label: 'rollup', phase: 'Rollup', agentType: 'general-purpose', model: 'opus', effort: 'high', schema: ROLLUP_SCHEMA }
)

return { refined: ok, rollup }
