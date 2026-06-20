
#!/bin/bash
echo "🚀 Checking update ..."
~/.local/bin/claude update

echo "🚀 Starting claude code irrestrict..."
claude --dangerously-skip-permissions \
    $@
