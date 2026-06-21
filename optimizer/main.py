#!/usr/bin/env python3
"""CLI entry point for cc-optimizer."""

import sys
import os
from pathlib import Path


def main():
    """Main entry point when installed via pip."""
    repo_root = Path(__file__).parent.parent

    # Handle subcommands
    if len(sys.argv) < 2:
        print("cc-optimizer — analyze Claude Code logs and optimize configuration")
        print()
        print("Usage: cc-optimizer <command> [args]")
        print()
        print("Commands:")
        print("  run          Run full pipeline (auto-detects ~/.claude/projects)")
        print("  analyze      Aggregate session transcripts")
        print("  cost         Weight findings by pricing")
        print("  recommend    Generate recommendations")
        print("  apply        Apply per-project config changes (dry-run default)")
        print("  inventory    Analyze MCP/plugin/connector usage (v2)")
        print("  apply2       Apply MCP/plugin/connector cleanups (dry-run default)")
        print("  report       Generate Markdown report")
        print()
        print("Run './run.sh' from the project directory for interactive use.")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        # Delegate to run.sh
        os.system(f"cd {repo_root} && ./run.sh")
    elif cmd == "analyze":
        # Direct Python call
        sys.argv.pop(0)  # Remove 'cc-optimizer'
        sys.argv[0] = str(repo_root / "optimizer" / "analyze.py")
        exec(open(sys.argv[0]).read())
    else:
        print(f"Unknown command: {cmd}")
        print("Run 'cc-optimizer' with no args for help.")
        sys.exit(1)


if __name__ == "__main__":
    main()
