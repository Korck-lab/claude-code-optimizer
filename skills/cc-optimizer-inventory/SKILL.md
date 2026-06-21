---
name: cc-optimizer:inventory
description: "v2: Analyze MCP/plugin/connector usage: compare what's exposed vs. what's actually used, generate per-project cleanup recommendations."
argument-hint: "[path/to/sessions] [output.json]"
allowed-tools: Bash, Read
---

# /cc-optimizer:inventory

**v2 feature:** Analyze exposed vs. used MCP servers, plugins, and Claude AI connectors per project.

## What it finds

- **Exposed**: Claude AI connectors + globally-enabled plugins + global MCP servers
- **Used**: actual `tool_use` and `Skill` calls from your transcripts
- **Gap**: what's loaded but never used (dead weight)

## Usage

```
/cc-optimizer:inventory ~/.claude/projects optimizer/out/inventory.json
```

## Output: inventory.json

```json
{
  "projects": {
    "dev-squad": {
      "exposed_connectors": ["gmail", "slack"],
      "used_connectors": ["slack"],
      "unused_connectors": ["gmail"],
      "exposed_plugins": ["@foo/mcp-server", "@bar/skills"],
      "used_plugins": ["@bar/skills"],
      "unused_plugins": ["@foo/mcp-server"],
      "recommendations": [
        { "type": "connector", "id": "gmail", "action": "disable" },
        { "type": "plugin", "id": "@foo/mcp-server", "action": "disable" }
      ]
    }
  }
}
```

## Next step

Apply recommendations with `/cc-optimizer:apply --apply` (writes `settings.local.json` per project).

## Safety guarantees

- **Worktree aggregation**: nested worktrees inherit parent settings; usage summed correctly
- **Inherited settings**: nested projects resolve parent config chains
- **Zero false positives**: verified against actual plugin catalogs in `~/.claude/plugins/cache/`
