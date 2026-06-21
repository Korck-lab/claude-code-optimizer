# Contributing to cc-optimizer

Thank you for considering a contribution. This document outlines guidelines for reporting issues and submitting improvements.

## Prerequisites

- Python 3.9 or later
- `pip` (for development dependencies)
- Claude Code (for understanding session transcripts)

## Development Setup

```bash
git clone https://github.com/Korck-lab/claude-code-optimizer
cd claude-code-optimizer

# Configure git hooks for automatic version bumping
git config core.hooksPath .githooks

# Install dev dependencies (optional)
pip install -r requirements-dev.txt

# Run syntax check
python3 -m py_compile optimizer/*.py

# Test with your own session logs
./run.sh ~/.claude/projects  # auto-detects logs
```

### Automatic Version Bumping

The pre-commit hook (`.githooks/pre-commit`) automatically bumps the patch version when you commit code changes. It updates:
- `VERSION` (source of truth)
- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `pyproject.toml`
- `optimizer/__init__.py`

The hook respects a skip guard: it won't bump on no-op amends or if only docs/comments changed. Run `git config core.hooksPath .githooks` after clone to enable it.

## Running Tests

The tool is validated with real Claude Code session transcripts. To test locally:

```bash
# Dry-run: preview analysis
./run.sh

# Single project
python3 optimizer/inventory.py ~/.claude/projects optimizer/out/inventory.json
python3 optimizer/apply2.py optimizer/out/inventory.json --project <slug>

# Apply changes (with backup)
python3 optimizer/apply2.py optimizer/out/inventory.json --apply --report optimizer/out/apply2-report.json
```

## Issue Types

### Bug Reports
- Include Python version (`python3 --version`)
- Include sample session log (sanitized if needed)
- Expected vs. actual behavior
- Traceback (if applicable)

### Feature Requests
- Describe the optimization opportunity
- Include token/cost impact estimate if known
- Suggest the config knob that would implement it
- Link to official Claude Code documentation

### Documentation
- Clarifications to README / CHANGELOG
- Examples of real use cases
- Links to official docs

## Code Style

- **Python**: standard PEP 8, 100-char line limit
- **Comments**: "why" not "what"; one-liner max
- **No external dependencies** beyond Python stdlib (to keep install lightweight)

## Submission

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/xyz`
3. Make changes with clear commit messages
4. Test with real session logs (sanitize sensitive data if needed)
5. Submit a pull request with a link to related issues

## Scope

cc-optimizer is a **survey/levantamento tool** — it analyzes and recommends, never applies changes without explicit user approval. We maintain this boundary strictly:

- ✅ New recommendation signals (new knobs to optimize)
- ✅ Better cost/pricing models
- ✅ Safety improvements (validation, dry-run coverage)
- ❌ Auto-apply mechanisms
- ❌ Integration into Claude Code CLI or plugins

## Questions?

Open an issue with the `[question]` prefix. The maintainer will respond within 2-3 days.

---

**Code of Conduct**: Be respectful, inclusive, and focused on technical merit.
