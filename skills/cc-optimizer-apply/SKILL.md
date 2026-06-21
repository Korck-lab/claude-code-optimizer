---
name: cc-optimizer:apply
description: "Preview or apply per-project config optimizations: model choice, subagent models, cache tuning (dry-run default, use --apply to write)."
argument-hint: "[--apply] [--project slug] [--min-confidence low|medium|high]"
allowed-tools: Bash, Read, Edit
---

# /cc-optimizer:apply

Convert findings into real `settings.json` edits. Safe by default (dry-run); review changes before applying.

## Usage

```
/cc-optimizer:apply                                # Dry-run: preview all changes
/cc-optimizer:apply --project metropolys           # Dry-run: preview one project
/cc-optimizer:apply --min-confidence low --apply   # Write all projects (including low-confidence)
/cc-optimizer:apply --project dev-squad --apply    # Write one project (with .bak backup)
```

## What it can auto-apply (safe, low-risk)

- `model: sonnet` / `model: haiku` — switch default model
- `CLAUDE_CODE_SUBAGENT_MODEL=haiku` — cheaper subagents
- `ENABLE_TOOL_SEARCH=false` — defer MCP tool-search
- Cache-prefix stabilizers (`ATTRIBUTION_HEADER=0`)

## What goes to MANUAL (you decide)

- Force 5m ephemeral cache (`FORCE_PROMPT_CACHING_5M`)
- Disable MCP servers per-project
- Other destructive/complex changes

## Safety

- Dry-run by default (shows plan, makes no changes)
- Per-file `.bak` backups before any write
- Deep-merge: never clobbers other keys
- On-disk verify: confirms intended mutations present

## Next step

If changes look good: `/cc-optimizer:apply --project slug --apply` to write one project.
