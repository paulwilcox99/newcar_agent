#!/usr/bin/env bash
# setup.sh — create virtual environment and install dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating virtual environment..."
python3 -m venv .venv

echo "==> Installing dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "==> Created .env from .env.example — add your OPENAI_API_KEY"
else
    echo "==> .env already exists — skipping"
fi

echo ""
echo "Setup complete. To run the agent:"
echo "  .venv/bin/python agent.py"
echo ""
echo "Options:"
echo "  --dry-run            Log decisions without writing to DB"
echo "  --poll-interval N    Seconds between state polls (default: 3)"
echo "  --db-dir PATH        Override DB directory"
