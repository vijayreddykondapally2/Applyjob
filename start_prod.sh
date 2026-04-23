#!/bin/bash
# ─────────────────────────────────────────────────────────────
# ApplyJob AI — Production Start Script (Gunicorn)
# ─────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "⚡ ApplyJob AI — Production Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "❌ No venv found. Run start_app.sh first for dev setup."
    exit 1
fi

# Install gunicorn if not present
./venv/bin/pip install -q gunicorn

# Initialize the database
./venv/bin/python -c "from app.database import init_db; init_db(); print('✅ Database ready')"

# Ensure data directory
mkdir -p data

echo ""
echo "🚀 Starting production server on http://0.0.0.0:5001"
echo "   Workers: 4 | Threads: 4 per worker"
echo "   Press Ctrl+C to stop"
echo ""

# Gunicorn with 4 workers × 4 threads = 16 concurrent requests
./venv/bin/gunicorn \
    --bind 0.0.0.0:5001 \
    --workers 4 \
    --threads 4 \
    --timeout 120 \
    --access-logfile data/access.log \
    --error-logfile data/error.log \
    --log-level info \
    web_app:app
