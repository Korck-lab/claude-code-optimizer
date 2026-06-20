# cc-optimizer

An **external, reusable system that analyzes Claude Code session logs and surfaces
per-project, *applyable* configuration optimizations to cut quota/token waste.**
It is a *levantamento* (survey) tool: it proposes concrete config changes with
estimated savings — it **does not apply anything**.

Different models burn quota very differently (Opus input is 5× Haiku, Fable 10×),
so the biggest levers are model choice, prompt-cache behavior, subagent model, and
context bloat. Every recommendation maps to a real config knob sourced from the
**current official docs** (never from model memory), with a $ estimate derived from
the **real token components already recorded in each transcript**.

## Why it's cheap to run

The transcripts already contain `message.usage` (input/output, cache read/creation,
1h vs 5m ephemeral) and `message.model` per turn. So the heavy analysis is **pure
Python arithmetic — zero model tokens**. LLMs are used only where judgment is needed:
- one-time **research** of official docs (Sonnet agents) → `knowledge-base.json`
- optional **per-project refinement** (Sonnet; Opus only for the top spenders)

## Pipeline

```
sessions/<project-slug>/<uuid>.jsonl         your copied Claude Code logs
        │
        ▼  analyze.py        deterministic aggregate  ── 0 model tokens
   raw-stats.json
        │
        ▼  cost.py           weight by fresh-docs pricing (knowledge-base.json)
   cost-stats.json
        │
        ▼  recommend.py      rules engine → actionable findings ($ + evidence)
   findings.json ───────────────────────────────► report.py → report.md
        │
        ▼  briefs.py         compact per-project briefs (no raw transcripts)
   out/briefs/*.json
        │
        ▼  cc-optimizer workflow   LLM refine (Opus top-5 / Sonnet rest) + rollup
   refine-result.json
        │
        ▼  merge.py          keep deterministic evidence + LLM tightening
   findings-final.json ─────────────────────────► report.py → report-final.md
```

## Run

```bash
# 1. put your logs under ./sessions (one subdir per project, *.jsonl inside)
# 2. deterministic levantamento (produces optimizer/out/report.md):
./run.sh                      # or: ./run.sh /path/to/sessions

# 3. (optional) LLM-refined findings — from a Claude Code session:
#    Workflow({ name: "cc-optimizer", args: { refineArgsPath: "<abs>/optimizer/out/refine-args.json" } })
#    then merge.py + report.py on findings-final.json (see run.sh tail).
```

`knowledge-base.json` is regenerated from live docs by the **cc-cost-research**
workflow — re-run it when Claude Code/pricing changes so nothing relies on memory.

## Apply (optional, you run it)

`apply.py` turns the findings into real `settings.json` edits. **Safe by default
(dry-run)**; it writes only with `--apply`. It auto-applies only unambiguous,
low-risk keys (subagent/default model, MCP tool-search deferral, cache-prefix
stabilizers) and lists conditional/destructive items (force-5m cache, MCP server
prune) under **MANUAL** for you to review. Project dirs are resolved from the
real `cwd` in the transcripts (the dominant root), never from the ambiguous slug.

```bash
python3 optimizer/apply.py                       # dry-run: preview the plan (min-confidence=medium)
python3 optimizer/apply.py --min-confidence low  # also include low-confidence upper-bound levers (opusplan)
python3 optimizer/apply.py --project dev-squad --apply   # write one project (deep-merge + .bak)
python3 optimizer/apply.py --apply --global      # write all + user-level ~/.claude/settings.json defaults
```

Applying changes YOUR environment — review the dry-run and start with one project.

## v2 — usage-driven MCP / plugin / connector cleanup (per project)

The biggest *non-token* waste is **stuff loaded into every project that the project
never uses**: claude.ai connectors, plugin-provided MCP servers, and plugin skills.
v2 finds and removes exactly those, **per project**, from real session evidence —
using only mechanisms verified to take effect at runtime (a previous attempt with
the unsupported `disabledMcpServers` key was a silent no-op; v2 does not repeat it).

```
~/.claude.json + ~/.claude/settings.json + ~/.claude/plugins   "what is EXPOSED"
sessions/<slug>/*.jsonl  (real tool_use / Skill calls)          "what is USED"
        │
        ▼  inventory.py        exposed − used  →  per-project recommendations
   out/inventory.json
        │
        ▼  apply2.py           write the 3 supported mechanisms (dry-run default)
   out/apply2-report.json
```

**What it computes.** For each project (one session slug → one project root, resolved
from the transcript `cwd`):

- **Exposed** = claude.ai connectors + globally-enabled plugins (`~/.claude/settings.json`
  `enabledPlugins`, incl. the `*@*: true` wildcard) + global user `mcpServers`.
- **Used** = real `tool_use` (`mcp__<server>__…`) and `Skill(skill="<plugin>:…")` calls
  parsed from the transcripts.
- **Capability** of each plugin (does it ship an MCP server? how many skills?) comes
  from a **static catalog** read off the plugin payloads in `~/.claude/plugins/cache/…`
  (`plugin.json` `mcpServers` + `.mcp.json` + `skills/`), so it flags dead weight that
  was *listed but never invoked* (e.g. a skills pack that bloats `/doctor`).

**Two correctness rules it enforces** (both caught real false-positives in testing):

- **Worktree aggregation** — `…/.claude/worktrees/<x>` is collapsed onto its parent and
  their usage summed, because worktrees inherit the parent's `settings.local.json`.
  Otherwise a plugin used only via worktrees would be wrongly disabled in the parent.
- **Inherited settings** — a nested project inherits every ancestor's `.claude` settings;
  `inventory.py` resolves that chain so it won't re-recommend a disable the parent already made.

**The three mechanisms `apply2.py` writes** (all per-project, all reversible):

| Recommendation | Edit |
|---|---|
| connectors, 0 use | `settings.json` → `"disableClaudeAiConnectors": true` |
| plugin, 0 use | `settings.local.json` → `enabledPlugins["<id>"] = false` (merge) |
| dead `disabledMcpServers` key | removed (it is an unsupported no-op) |

```bash
python3 optimizer/inventory.py sessions optimizer/out/inventory.json        # build the cross-reference
python3 optimizer/apply2.py optimizer/out/inventory.json                     # dry-run: per-project plan
python3 optimizer/apply2.py optimizer/out/inventory.json --project foo       # preview one project
python3 optimizer/apply2.py optimizer/out/inventory.json --apply --report optimizer/out/apply2-report.json
```

**Safety.** Dry-run by default. Per touched file: timestamped `.bak`, deep-merge (never
clobbers other keys), and on-disk verify (intended mutations present, **zero collateral**).
Ephemeral worktrees and deleted/temp project dirs are skipped. The **global
`~/.claude/settings.json` is never written**. Guarantee checked on every run: it never
disables a plugin with >0 aggregated use. After applying, re-run `inventory.py` — applied
projects should drop to **0 recommendations** (idempotent = the change took effect); for
UI-level proof reopen `/mcp` and `/doctor` in a couple of projects.

## What counts as a finding

Only an **applyable config change** with (a) a concrete knob + location, (b) a
concrete value for *that* project, (c) an estimated $ saving from its real tokens,
(d) auditable evidence. Pure advice/tips are excluded by design (see
`knowledge-base.json → excluded_advisory`). Confidence and UPPER-BOUND labels are
carried through honestly; estimates are a model, not a bill.

## Detection signals (→ knob)

| Signal (measured from `usage`) | Knob |
|---|---|
| Subagents on expensive models | `CLAUDE_CODE_SUBAGENT_MODEL=haiku` |
| Expensive default model on trivial turns | `model: sonnet` / `opusplan` |
| 1h-cache write overuse | `FORCE_PROMPT_CACHING_5M` |
| MCP tool-schema / context bloat | `ENABLE_TOOL_SEARCH` + prune servers |
| Low prompt-cache hit ratio | stabilize prefix (`ATTRIBUTION_HEADER=0`, `DISABLE_AUTOUPDATER`) |

## Layout

```
run.sh                       orchestrator (deterministic pipeline)
optimizer/analyze.py         aggregate transcripts → raw-stats.json
optimizer/cost.py            $ weighting from KB pricing
optimizer/recommend.py       rules engine → findings.json
optimizer/briefs.py          compact per-project briefs
optimizer/merge.py           deterministic + LLM merge → findings-final.json
optimizer/report.py          findings → Markdown report
optimizer/apply.py           findings → per-project settings.json (dry-run default)
optimizer/inventory.py       v2: exposed × used → per-project MCP/plugin/connector recs
optimizer/apply2.py          v2: write disableClaudeAiConnectors / enabledPlugins:false (dry-run default)
optimizer/knowledge-base.json   pricing + actionable knobs + signals (fresh docs)
optimizer/out/               generated artifacts (gitignored)
.claude/workflows/cc-optimizer.js   the LLM refinement workflow
sessions/                    your logs (gitignored)
```

Nothing here is applied to your machine. Scope is **levantamento only**.
