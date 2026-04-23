#!/bin/bash
# ─────────────────────────────────────────────────────────────
# ApplyJob AI — Local Development Start Script
# ─────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "⚡ ApplyJob AI — Multi-User Development Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
echo "📦 Installing dependencies..."
./venv/bin/pip install -q -r requirements.txt

# Install playwright browsers if not already done
if [ ! -d "venv/lib/python3.*/site-packages/playwright" ] 2>/dev/null; then
    echo "🌐 Installing Playwright browsers..."
    ./venv/bin/playwright install chromium
fi

# Ensure data directory exists
mkdir -p data

echo ""
echo "🚀 Starting web server on http://localhost:5001"
echo "   Press Ctrl+C to stop"
echo ""

./venv/bin/python web_app.py
