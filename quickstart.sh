#!/usr/bin/env bash
# quickstart.sh — one-command setup + launch, using demo data.
#
#   git clone <this repo>
#   cd market-announcements-suite
#   ./quickstart.sh
#
# What it does:
#   1. Creates/activates a local .venv
#   2. Installs requirements.txt
#   3. Builds a small synthetic bse_equity.db (no scraping / no internet
#      needed) so you have something to look at immediately
#   4. Scores it with the Idea Board pipeline
#   5. Launches the Streamlit dashboard
#
# To use REAL data instead of the demo DB, run this once first:
#   python3 market_announcements.py fetch
# (see README.md for details) — then re-run ./quickstart.sh; it will not
# overwrite an existing bse_equity.db.

set -e
cd "$(dirname "$0")"

echo "── 1/5  Python virtual environment ─────────────────────────"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "── 2/5  Installing dependencies ────────────────────────────"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "── 3/5  Demo data ──────────────────────────────────────────"
if [ ! -f "bse_equity.db" ]; then
    echo "    No bse_equity.db found — building a small synthetic demo dataset."
    python3 announcement_ideas_pipeline.py --db bse_equity.db demo
else
    echo "    bse_equity.db already exists — leaving it as-is."
fi

echo "── 4/5  Scoring announcements (Idea Board) ─────────────────"
python3 announcement_ideas_pipeline.py --db bse_equity.db run

echo "── 5/5  Launching dashboard ─────────────────────────────────"
echo "    Login with  admin / admin@123  (change it after first login)"
streamlit run bse_market_suite.py
