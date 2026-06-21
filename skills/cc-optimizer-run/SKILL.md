---
name: cc-optimizer:run
description: "Run full cc-optimizer pipeline: analyze Claude Code logs, identify quota waste, generate findings report with $ estimates. Auto-detects ~/.claude/projects (zero-config)."
argument-hint: "[path/to/sessions]"
allowed-tools: Bash, Read
---

# /cc-optimizer:run

Run the complete cc-optimizer pipeline to analyze your Claude Code session logs and generate a findings report.

## What it does

1. **Scans** your Claude Code session history (auto-detects `~/.claude/projects`)
2. **Aggregates** token usage, models, cache metrics per project
3. **Weights** findings by current Claude Code pricing
4. **Generates** actionable recommendations with $ estimates
5. **Outputs** `optimizer/out/report.md` with findings and evidence

## Usage

```
/cc-optimizer:run                    # Auto-detect ~/.claude/projects
/cc-optimizer:run /path/to/sessions  # Custom sessions directory
```

## Output

```
optimizer/out/
  raw-stats.json      Raw aggregates (no tokens spent)
  cost-stats.json     Findings weighted by pricing
  findings.json       Actionable recommendations
  report.md           Markdown report (main output)
```

## Next steps

- Review `optimizer/out/report.md` for findings
- Run `/cc-optimizer:apply` to preview config changes
- Run `/cc-optimizer:inventory` to analyze MCP/plugin usage (v2)

## Zero-config

If you have Claude Code installed with session history, `/cc-optimizer:run` works instantly — no setup needed.
