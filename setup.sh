#!/bin/bash
# One-time setup: create venv, install deps, copy .env.example -> .env if missing.
# Idempotent — safe to re-run.

set -e

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        echo "Created .env from .env.example."
        echo "==> Edit .env now with your credentials, then re-run ./setup.sh"
        exit 0
    fi
fi

if [[ ! -d .venv ]]; then
    echo "Creating virtualenv..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

echo
echo "Setup complete."
echo
echo "Next steps:"
echo "  1. Make sure the Claude Code CLI is installed and authenticated:"
echo "       npm install -g @anthropic-ai/claude-code"
echo "       claude   # log in interactively"
echo
echo "  2. Run the audit once manually to capture cookies + verify email:"
echo "       source .venv/bin/activate && python audit.py"
echo
echo "  3. To schedule daily at 7am:"
echo "       cp com.zach.fantrax.audit.plist ~/Library/LaunchAgents/"
echo "       launchctl load ~/Library/LaunchAgents/com.zach.fantrax.audit.plist"
