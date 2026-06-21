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

### Option 1: Claude Code Plugin (recommended)

The easiest way to use cc-optimizer is as a Claude Code plugin with built-in commands:

1. In Claude Code, run `/plugin`
2. Click **Marketplace** → **New**
3. Enter: `@Korck-lab/claude-code-optimizer`
4. Click **Install**

Then use the plugin commands directly in Claude Code:

```
/cc-optimizer:run        # Analyze your session logs
/cc-optimizer:apply      # Preview config changes (dry-run)
/cc-optimizer:inventory  # Analyze MCP/plugin usage (v2)
```

### Option 2: CLI Standalone

Install via pip for command-line use:

```bash
pip install claude-code-optimizer
cc-optimizer run  # runs with auto-detected ~/.claude/projects
```

### Option 3: From Source

```bash
git clone https://github.com/Korck-lab/claude-code-optimizer
cd claude-code-optimizer
pip install -e .
./run.sh  # or: python3 optimizer/analyze.py ~/.claude/projects optimizer/out/raw-stats.json
```

## Quick start

### Using the Claude Code Plugin (easiest)

Once installed:

```
/cc-optimizer:run        # Scan logs, analyze, generate report
                         # → optimizer/out/report.md
```

Review the findings in the report. Then:

```
/cc-optimizer:apply      # Preview config changes (dry-run)
/cc-optimizer:apply --apply --project slug  # Write changes (with .bak backup)
```

For MCP/plugin cleanup (v2):

```
/cc-optimizer:inventory  # Find unused plugins/connectors
```

### Using the CLI (standalone)

If you prefer command-line:

```bash
./run.sh                 # Analyze logs (auto-detects ~/.claude/projects)
# Output: optimizer/out/report.md

python3 optimizer/apply.py         # Preview changes
python3 optimizer/apply.py --apply # Apply all projects
```

### Step-by-step workflow

1. **Analyze** — `/cc-optimizer:run` scans your session history
2. **Review** — Read `optimizer/out/report.md` for findings and $ estimates
3. **Dry-run** — `/cc-optimizer:apply` to preview what would change
4. **Apply** — `/cc-optimizer:apply --apply` (with timestamped `.bak` backups)
5. **Verify** — Re-run analysis to confirm applied changes took effect

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

## Plugin Commands (Claude Code)

When installed as a plugin, use these commands directly in Claude Code:

### /cc-optimizer:run

Run the full optimization pipeline. Auto-detects `~/.claude/projects`.

```
/cc-optimizer:run                    # Analyze your session logs
/cc-optimizer:run /path/to/sessions  # Custom path
```

Outputs: `optimizer/out/report.md` (main findings report)

### /cc-optimizer:analyze

Low-level analyzer — aggregate transcripts into per-project statistics (pure arithmetic, zero model tokens).

```
/cc-optimizer:analyze ~/.claude/projects optimizer/out/raw-stats.json
```

### /cc-optimizer:apply

Preview or apply per-project config changes. Safe by default (dry-run).

```
/cc-optimizer:apply                          # Dry-run: preview all changes
/cc-optimizer:apply --project dev-squad      # Dry-run: preview one project
/cc-optimizer:apply --apply                  # Write all projects (with .bak)
/cc-optimizer:apply --project dev-squad --apply  # Write one project
```

### /cc-optimizer:inventory

Analyze MCP/plugin/connector usage (v2): find what's loaded but never used.

```
/cc-optimizer:inventory ~/.claude/projects optimizer/out/inventory.json
```

## CLI Commands (Standalone)

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
