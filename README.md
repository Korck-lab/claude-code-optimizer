# cc-optimizer

Analyze your Claude Code session logs and discover per-project configuration optimizations to cut token waste.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/downloads/)

## What it does

- 🔍 **Scans your Claude Code session history** (~/.claude/projects) — automatic, zero-config
- 📊 **Identifies per-project quota waste**: expensive models on trivial work, bloated MCP servers, unused plugins, cache misses
- 💰 **Generates actionable findings with $ estimates** based on your real token usage
- ✏️ **Writes safe, reversible config changes** (dry-run by default, you review before applying)
- 🔒 **Never modifies settings without approval** — requires explicit `--apply` flag

## Key insights

Different models burn budget very differently:
- **Opus input tokens**: 5× more expensive than Haiku
- **Fable**: 10× more expensive than Haiku per input token

Biggest optimization levers:
1. **Model choice** — use Haiku for simple tasks, Opus only for complex reasoning
2. **Prompt caching** — stabilize your context prefixes to hit cache more often
3. **Subagent models** — task-specific model selection for background work
4. **MCP/plugin bloat** — remove tools and plugins unused in each project

Every recommendation maps to a real Claude Code config knob from official documentation, with estimates derived from your actual recorded token usage.

## Installation

### Via pip (recommended)

```bash
pip install claude-code-optimizer
cc-optimizer run  # runs with auto-detected ~/.claude/projects
```

### From source

```bash
git clone https://github.com/rafaelaguilherdacosta/claude-code-optimizer
cd claude-code-optimizer
pip install -e .
```

## Quick start

### 1. Analyze your session logs (zero-config)

```bash
./run.sh
# or: python3 optimizer/analyze.py ~/.claude/projects optimizer/out/raw-stats.json
```

The tool automatically detects your Claude Code session history at `~/.claude/projects` (created by Claude Code when you use it). No setup needed.

Output: `optimizer/out/report.md` — a markdown report of findings and estimates.

### 2. Review findings (dry-run)

```bash
python3 optimizer/apply.py
# Preview what changes would be applied (no modifications yet)
```

### 3. Apply changes (one project at a time)

```bash
# Single project — review the dry-run first
python3 optimizer/apply.py --project your-project-slug --apply

# All projects (with backups)
python3 optimizer/apply.py --apply --global
```

Each project gets a timestamped `.bak` file. Changes are deep-merged into existing configs — nothing is lost.

## Examples

### Example 1: Expensive model on trivial work

**Finding:**
```
Project: my-web-app
Opus model used on 120 small turns (< 300 output tokens)
Recommendation: switch to Sonnet (3.5 hours ≈ $0.47 saved)
Knob: model: sonnet in settings.json
```

**Why:** Small turns don't need Opus reasoning. Sonnet is 3× cheaper and still capable.

### Example 2: Unused plugin in every project

**Finding:**
```
Plugin @foo/mcp-server exposed to 50 projects, used in 2
Recommendation: disable in 48 projects (saves 2.3 hours ≈ $1.20)
Knob: enabledPlugins["@foo/mcp-server"] = false in settings.local.json
```

**Why:** Each loaded plugin slows down `/mcp` introspection and increases context overhead. Remove what you don't use.

### Example 3: Cache-unfriendly prefix

**Finding:**
```
Cache creation tokens: 450KB (1h pool)
Cache read tokens: 120KB (1h pool)
Hit ratio: 21% (low)
Recommendation: stabilize context prefix (ATTRIBUTION_HEADER=0)
Estimated: +12% cache hit rate ≈ $0.30/month saved
```

**Why:** Every unique header in your assistant messages ruins cache hits. Stabilize your context to reuse cache across sessions.

## How it works

### Pipeline

```
~/.claude/projects/                          Your Claude Code session logs
        ↓
   analyze.py          Aggregate by project, model, token usage
        ↓
   raw-stats.json      Per-project summaries (token, cache, model metrics)
        ↓
   cost.py             Weight findings by current official pricing
        ↓
   cost-stats.json     Finds with $ estimates
        ↓
   recommend.py        Rules engine → actionable recommendations
        ↓
   findings.json ──→ report.py ──→ report.md
        ↓
   (optional)
   briefs.py          Per-project summaries for LLM refinement
        ↓
   cc-optimizer workflow  LLM tightens findings (Opus/Sonnet)
        ↓
   merge.py           Deterministic + LLM refinement
        ↓
   findings-final.json ──→ report-final.md
```

### Core features

**Why it's fast:**
- Pure Python arithmetic on recorded usage data — zero model tokens for analysis
- LLMs only used for optional refinement of top spenders
- Typical run: < 5 seconds on 100 projects

**What counts as a finding:**
- Must be applyable: real config knob + location + concrete value
- Must have evidence: derived from your actual token usage
- Must have a $ estimate: from current official pricing
- Pure advice (tips, patterns) excluded by design

**Safety guarantees:**
- Dry-run by default — preview changes before applying
- Per-file timestamped `.bak` backups before any write
- Deep-merge: never clobbers other config keys
- On-disk verification: inspects actual mutations
- Global `~/.claude/settings.json` never modified (only per-project)
- Idempotent: re-run analysis after applying — should show 0 recommendations

### Detection signals

| Signal (from your usage data) | Config knob | Impact |
|---|---|---|
| Expensive model on trivial output | `model: sonnet` / `model: haiku` | 3-5× cost reduction |
| Subagents on expensive models | `CLAUDE_CODE_SUBAGENT_MODEL=haiku` | 5× cost reduction |
| Low prompt-cache hit ratio | `ATTRIBUTION_HEADER=0` (stabilize prefix) | +10-15% cache hits |
| 1h-cache write overuse | `FORCE_PROMPT_CACHING_5M` | Reduce ephemeral churn |
| MCP context bloat | Disable unused MCP servers | Faster tool search, less context |
| Unused plugins per project | `enabledPlugins[id]=false` | Cleaner /doctor output, faster startup |

## v2: Per-project MCP / plugin / connector cleanup

The largest non-token waste is **stuff loaded into every project that you never use**: Claude AI connectors, globally-enabled plugins, and MCP servers.

v2 analyzes real tool usage from your transcripts:

```
~/.claude/plugins/cache/          What plugins are installed
~/.claude/settings.json           What's globally enabled
~/.claude/projects/*.jsonl        What you actually used (tool_use / Skill calls)
        ↓
   inventory.py        exposed − used = recommendations per project
        ↓
   apply2.py           Write reversible, safe cleanups
```

**Results:**
- Disable unused connectors: `disableClaudeAiConnectors: true`
- Disable unused plugins per-project: `enabledPlugins[id]: false` in `settings.local.json`
- Remove dead `disabledMcpServers` keys (unsupported, no-op)

**Correctness:**
- **Worktree aggregation**: nested worktrees inherit parent settings; usage is correctly summed
- **Inherited settings**: nested projects resolve parent config chains
- **Zero false positives**: verified against actual plugin catalogs

## Commands

### analyze.py — aggregate transcripts

```bash
python3 optimizer/analyze.py ~/.claude/projects optimizer/out/raw-stats.json
```

Reads all `.jsonl` files from your Claude Code projects, produces token/model/cache aggregates per project.

### cost.py — weight by pricing

```bash
python3 optimizer/cost.py optimizer/out/raw-stats.json optimizer/knowledge-base.json optimizer/out/cost-stats.json
```

Converts usage data to $ estimates using current official Claude pricing.

### recommend.py — rules engine

```bash
python3 optimizer/recommend.py optimizer/out/raw-stats.json optimizer/knowledge-base.json optimizer/out/findings.json
```

Generates actionable recommendations with confidence levels and evidence.

### apply.py — write per-project settings

```bash
python3 optimizer/apply.py                       # dry-run: preview plan
python3 optimizer/apply.py --project foo         # preview one project
python3 optimizer/apply.py --apply               # write all projects (with .bak)
python3 optimizer/apply.py --apply --global      # also update ~/.claude/settings.json defaults
```

Writes safe, deep-merged config changes. Timestamped backups created before any mutation.

### inventory.py — MCP/plugin usage analysis

```bash
python3 optimizer/inventory.py ~/.claude/projects optimizer/out/inventory.json
```

Builds exposed vs. used inventory for connectors, plugins, and MCP servers.

### apply2.py — write MCP/plugin cleanups

```bash
python3 optimizer/apply2.py optimizer/out/inventory.json                     # dry-run
python3 optimizer/apply2.py optimizer/out/inventory.json --apply             # write all
python3 optimizer/apply2.py optimizer/out/inventory.json --project foo --apply # one project
```

Applies per-project disables for unused connectors/plugins. Safe, reversible, dry-run by default.

## Project layout

```
run.sh                            Main orchestrator (deterministic pipeline)
optimizer/
  analyze.py                      Aggregate transcripts → raw-stats.json
  cost.py                         $ weighting from knowledge-base.json
  recommend.py                    Rules engine → findings.json
  apply.py                        findings → per-project settings.json edits
  inventory.py                    Exposed vs. used (v2: plugins/MCP/connectors)
  apply2.py                       Write disableClaudeAiConnectors / enabledPlugins:false
  briefs.py                       Compact summaries for LLM refinement
  merge.py                        Deterministic + LLM merge
  report.py                       Findings → Markdown
  verify_apply.py                 Safety verification utilities
  knowledge-base.json             Pricing + config knobs + signals (regenerated from official docs)
  out/                            Generated artifacts (gitignored)
.claude/workflows/cc-optimizer.js Optional LLM refinement workflow
LICENSE                           MIT
```

## Configuration

No configuration needed. The tool reads:
- `~/.claude/projects/` — your session history (auto-detected)
- `~/.claude/settings.json` — your current config
- `~/.claude/plugins/cache/` — installed plugin metadata
- `optimizer/knowledge-base.json` — official pricing & knobs

To refresh pricing when Claude Code/models change:
- Run the `cc-cost-research` workflow (standalone) to regenerate `knowledge-base.json`

## Requirements

- **Python 3.9+**
- Claude Code (any recent version; used only for session history)
- Your session history (`~/.claude/projects/` — automatically created by Claude Code)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting issues, submitting features, and development setup.

## License

MIT © 2026 cc-optimizer contributors. See [LICENSE](LICENSE).

---

**Questions?**  
Open an issue on [GitHub](https://github.com/rafaelaguilherdacosta/claude-code-optimizer/issues) with the `[question]` prefix.

**Want to learn more?**  
- [CHANGELOG](CHANGELOG.md) — version history and features
- [Official Claude Code docs](https://claude.com/code) — session export, settings, plugins
