---
name: cc-optimizer:analyze
description: "Analyze Claude Code transcripts: aggregate token usage, models, cache metrics per project (zero model tokens spent, pure arithmetic)."
argument-hint: "[path/to/sessions] [output.json]"
allowed-tools: Bash, Read
---

# /cc-optimizer:analyze

Low-level analyzer: aggregates Claude Code session transcripts into per-project statistics without spending any model tokens.

## Usage

```
/cc-optimizer:analyze                                          # Uses ~/.claude/projects
/cc-optimizer:analyze /path/to/sessions optimizer/out/raw-stats.json
```

## Output: raw-stats.json

```json
{
  "projects": {
    "project-slug": {
      "sessions": 150,
      "total_tokens": 8048909762,
      "input_tokens": 156819318,
      "output_tokens": 257451667,
      "cache_creation": 2150345336,
      "cache_read": 30117123696,
      "cache_hit_ratio": 0.9288,
      "by_model": {
        "claude-opus-4-8": { "msgs": 5000, "tokens": 18012088391 },
        ...
      }
    }
  }
}
```

## What's measured (from your transcripts)

- Total tokens by model
- Cache creation vs. read (1h vs 5m ephemeral)
- Cache hit ratio
- Output-size histogram (identifies trivial work on expensive models)
- Subagent vs. main task spending

## What's NOT spent

- Zero model API calls — pure Python arithmetic on recorded `message.usage` data
- All heavy lifting is deterministic, reusable, reproducible
