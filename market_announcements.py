#!/usr/bin/env python3
"""
market_announcements.py
=======================
Unified Indian Market Announcements Scraper
Fetches & stores data from 4 independent sources, each with its own SQLite DB:

  DB 1 → bse_equity.db      : BSE Equity announcements      (via bse-india lib)
  DB 2 → bse_sme.db         : BSE SME announcements + corp actions (via bsesme.com HTML)
  DB 3 → nse_equity.db      : NSE Equity announcements      (via NSE API)
  DB 4 → nse_sme.db         : NSE SME announcements         (via NSE API)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Install:
    pip install bse curl_cffi requests beautifulsoup4 lxml schedule

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

USAGE — source flag:  --source bse_equity | bse_sme | nse_equity | nse_sme | all

  # Captures today automatically (new default)
  python market_announcements.py fetch

  # Yesterday only (explicit)
  python market_announcements.py fetch --yesterday

  # Fetch one source
  python market_announcements.py fetch --source bse_equity
  python market_announcements.py fetch --source nse_sme

  # Fetch with date range
  python market_announcements.py fetch --from 01-06-2026 --to 28-06-2026
  python market_announcements.py fetch --source nse_equity --from 01-06-2026 --to 28-06-2026

  # BSE Equity – watchlist mode (specific symbols)
  python market_announcements.py fetch --source bse_equity --symbols TCS INFY ITC

  # Query stored data
  python market_announcements.py query --source bse_equity --from 01-06-2026 --to 28-06-2026
  python market_announcements.py query --source bse_sme --scrip ABCINFRA
  python market_announcements.py query --source nse_equity --output results.json

  # Generate text reports
  python market_announcements.py report --from 01-06-2026 --to 28-06-2026
  python market_announcements.py report --source nse_sme

  # Database statistics
  python market_announcements.py stats
  python market_announcements.py stats --source bse_sme

  # Export to CSV
  python market_announcements.py export out.csv
  python market_announcements.py export out.csv --source nse_equity

  # Unattended scheduled fetching (cron / Task Scheduler friendly)
  python market_announcements.py schedule --every hourly
  python market_announcements.py schedule --every daily --source nse_equity

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cron example (all sources, every 3 hours):
    0 */3 * * *  cd /path/to && python3 market_announcements.py fetch >> cron.log 2>&1

Windows Task Scheduler:
    Action    : python.exe
    Arguments : C:\\path\\to\\market_announcements.py fetch
    Trigger   : Repeat every 3 hours
"""

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION  — edit this block to customise behaviour
# ─────────────────────────────────────────────────────────────────────────────

# DB file paths (one per source)
DB_PATHS = {
    "bse_equity": "bse_equity.db",
    "bse_sme":    "bse_sme.db",
    "nse_equity": "nse_equity.db",
    "nse_sme":    "nse_sme.db",
}

REPORTS_DIR              = "reports"          # folder for text reports
LOG_FILE                 = "market_ann.log"   # unified log file

MAX_RETRIES              = 3
RETRY_DELAY_SECONDS      = 10

# BSE Equity attachment base URL — ATTACHMENTNAME from the API is just a
# filename (e.g. "abc123.pdf"); the actual document lives at this prefix.
BSE_EQ_ATTACH_BASE       = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

# BSE SME scraping config
BSE_SME_MAX_PAGES        = 100
BSE_SME_REQUEST_TIMEOUT  = 30
BSE_SME_RETRY_BACKOFF    = 5
BSE_SME_PAGE_SLEEP       = 1.0
SCHEDULE_INTERVAL_MINS   = 60    # for `schedule` sub-command

ALL_SOURCES = ["bse_equity", "bse_sme", "nse_equity", "nse_sme"]

# ─────────────────────────────────────────────────────────────────────────────
#  IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import time
import traceback
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING  (single log for all scrapers)
# ─────────────────────────────────────────────────────────────────────────────

Path(REPORTS_DIR).mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

log = logging.getLogger("market_ann")


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED DATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_ddmmyyyy(s: str) -> datetime:
    return datetime.strptime(s, "%d-%m-%Y")

def fmt_ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")

def today_str() -> str:
    return datetime.today().strftime("%d-%m-%Y")

def month_chunks(from_date: str, to_date: str):
    """Yield (from, to) DD-MM-YYYY pairs, one calendar month at a time."""
    start = parse_ddmmyyyy(from_date)
    end   = parse_ddmmyyyy(to_date)
    cur   = start
    while cur <= end:
        last_day  = monthrange(cur.year, cur.month)[1]
        chunk_end = min(cur.replace(day=last_day), end)
        yield fmt_ddmmyyyy(cur), fmt_ddmmyyyy(chunk_end)
        cur = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)

def needs_chunking(from_date: str, to_date: str, threshold: int = 30) -> bool:
    return (parse_ddmmyyyy(to_date) - parse_ddmmyyyy(from_date)).days > threshold

def day_chunks(from_date: str, to_date: str):
    """Yield (from, to) DD-MM-YYYY pairs, one calendar day at a time.

    BSE Equity's market-wide announcements endpoint (no scripcode) can
    return 2000+ rows / 50+ paginated requests for a SINGLE day. Sending a
    multi-week range as one call risks a mid-pagination timeout/rate-limit
    that discards everything fetched so far. Chunking daily, and storing
    after each day, means a failure only costs that one day, not the
    whole range.
    """
    start = parse_ddmmyyyy(from_date)
    end   = parse_ddmmyyyy(to_date)
    cur   = start
    while cur <= end:
        yield fmt_ddmmyyyy(cur), fmt_ddmmyyyy(cur)
        cur += timedelta(days=1)

def to_iso(ddmmyyyy_or_raw: str) -> str:
    """Parse many date formats → YYYY-MM-DD. Returns '' on failure."""
    raw = (ddmmyyyy_or_raw or "").strip()
    if not raw or raw in ("-", "N/A", "NA", "--", "n/a"):
        return ""
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y",
                "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED DB UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def open_db(path: str):
    """Context-managed SQLite connection with WAL mode, auto-commit/rollback."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _exec_script(path: str, script: str):
    """Run a DDL script on the given DB file."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  ████████████████████████████████████████████████████████████████████████████
#  DB 1 — BSE EQUITY  (bse_equity.db)
#  Uses the `bse` Python library to hit BSE's official announcements endpoint.
#  ████████████████████████████████████████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

BSE_EQUITY_DDL = """
CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS subcategories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name        TEXT NOT NULL,
    UNIQUE (category_id, name)
);
CREATE INDEX IF NOT EXISTS idx_bse_eq_subcat_category ON subcategories(category_id);

CREATE TABLE IF NOT EXISTS announcements (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id        TEXT,
    scrip_code       TEXT,
    symbol           TEXT,
    company_name     TEXT,
    category_id      INTEGER REFERENCES categories(id),
    subcategory_id   INTEGER REFERENCES subcategories(id),
    subject          TEXT,
    file_name        TEXT,
    input_timestamp  TEXT,
    attachment_url   TEXT,
    fetched_at       TEXT DEFAULT (datetime('now','localtime')),
    raw_json         TEXT,
    UNIQUE (script_id, file_name, input_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_bse_eq_code      ON announcements(scrip_code);
CREATE INDEX IF NOT EXISTS idx_bse_eq_symbol    ON announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_bse_eq_timestamp ON announcements(input_timestamp);

-- NOTE: the backward-compatible v_announcements view (plain "category" /
-- "subcategory" text columns for every existing query/report/export
-- function) is created in _bse_equity_migrate(), AFTER any legacy-table
-- rebuild -- not here. Creating it here would make SQLite choke when a
-- legacy DB's announcements table gets dropped/renamed during migration,
-- since the view would already be pinned to the old table.

CREATE TABLE IF NOT EXISTS run_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT,
    records_fetched  INTEGER DEFAULT 0,
    records_inserted INTEGER DEFAULT 0,
    error_message    TEXT
);
"""


def _bse_equity_migrate():
    """
    Older bse_equity.db files may have been created before:
      (a) the UNIQUE constraint existed on
          announcements(script_id, file_name, input_timestamp), and
      (b) category/subcategory were normalized into lookup tables.
    CREATE TABLE IF NOT EXISTS does not retrofit an existing table, so this
    function upgrades those DBs in place, without losing any data:

      1. Deletes any duplicate rows already in the table (keeps the lowest id).
      2. If the table still has the old free-text `category`/`subcategory`
         columns, normalizes their distinct values into `categories` /
         `subcategories`, points every row at the right ids via new
         category_id/subcategory_id columns, then rebuilds the table without
         the old text columns (SQLite can't ALTER..DROP COLUMN with a FK-bearing
         rebuild reliably pre-3.35, so a full rebuild is the safe path).
      3. Creates a real UNIQUE INDEX so writes actually de-dupe going forward,
         regardless of how the table was originally created.
      4. (Re)creates the v_announcements view so every existing query/report/
         export function keeps working against plain "category"/"subcategory"
         columns without any changes on their end.
    """
    path = DB_PATHS["bse_equity"]
    conn = sqlite3.connect(path)
    try:
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "announcements" not in existing_tables:
            return

        before = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        conn.execute("""
            DELETE FROM announcements
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM announcements
                GROUP BY script_id, file_name, input_timestamp
            )
        """)
        removed = before - conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        if removed:
            log.info("[BSE_EQUITY] Migration: removed %d duplicate row(s)", removed)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(announcements)").fetchall()}

        if "category" in cols and "category_id" not in cols:
            log.info("[BSE_EQUITY] Migration: normalizing category/subcategory into lookup tables")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subcategories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL REFERENCES categories(id),
                    name        TEXT NOT NULL,
                    UNIQUE (category_id, name)
                )
            """)
            conn.execute("ALTER TABLE announcements ADD COLUMN category_id INTEGER REFERENCES categories(id)")
            conn.execute("ALTER TABLE announcements ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)")

            conn.execute("""
                INSERT OR IGNORE INTO categories (name)
                SELECT DISTINCT TRIM(category) FROM announcements
                WHERE category IS NOT NULL AND TRIM(category) != ''
            """)
            conn.execute("""
                INSERT OR IGNORE INTO subcategories (category_id, name)
                SELECT DISTINCT c.id, TRIM(a.subcategory)
                FROM announcements a
                JOIN categories c ON c.name = TRIM(a.category)
                WHERE a.subcategory IS NOT NULL AND TRIM(a.subcategory) != ''
            """)
            conn.execute("""
                UPDATE announcements
                SET category_id = (SELECT id FROM categories WHERE name = TRIM(announcements.category))
                WHERE category IS NOT NULL AND TRIM(category) != ''
            """)
            conn.execute("""
                UPDATE announcements
                SET subcategory_id = (
                    SELECT sc.id FROM subcategories sc
                    JOIN categories c ON c.id = sc.category_id
                    WHERE c.name = TRIM(announcements.category)
                      AND sc.name = TRIM(announcements.subcategory)
                )
                WHERE subcategory IS NOT NULL AND TRIM(subcategory) != ''
            """)

            conn.execute("""
                CREATE TABLE announcements_new (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    script_id        TEXT,
                    scrip_code       TEXT,
                    symbol           TEXT,
                    company_name     TEXT,
                    category_id      INTEGER REFERENCES categories(id),
                    subcategory_id   INTEGER REFERENCES subcategories(id),
                    subject          TEXT,
                    file_name        TEXT,
                    input_timestamp  TEXT,
                    attachment_url   TEXT,
                    fetched_at       TEXT DEFAULT (datetime('now','localtime')),
                    raw_json         TEXT,
                    UNIQUE (script_id, file_name, input_timestamp)
                )
            """)
            conn.execute("""
                INSERT INTO announcements_new
                    (id, script_id, scrip_code, symbol, company_name, category_id,
                     subcategory_id, subject, file_name, input_timestamp,
                     attachment_url, fetched_at, raw_json)
                SELECT id, script_id, scrip_code, symbol, company_name, category_id,
                       subcategory_id, subject, file_name, input_timestamp,
                       attachment_url, fetched_at, raw_json
                FROM announcements
            """)
            conn.execute("DROP TABLE announcements")
            conn.execute("ALTER TABLE announcements_new RENAME TO announcements")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bse_eq_code      ON announcements(scrip_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bse_eq_symbol    ON announcements(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bse_eq_timestamp ON announcements(input_timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bse_eq_subcat_category ON subcategories(category_id)")
            log.info("[BSE_EQUITY] Migration: category/subcategory normalized")

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bse_eq_unique
            ON announcements(script_id, file_name, input_timestamp)
        """)
        conn.execute("""
            CREATE VIEW IF NOT EXISTS v_announcements AS
            SELECT
                a.id, a.script_id, a.scrip_code, a.symbol, a.company_name,
                c.name  AS category,
                sc.name AS subcategory,
                a.subject, a.file_name, a.input_timestamp, a.attachment_url,
                a.fetched_at, a.raw_json
            FROM announcements a
            LEFT JOIN categories    c  ON c.id  = a.category_id
            LEFT JOIN subcategories sc ON sc.id = a.subcategory_id
        """)
        conn.commit()
    finally:
        conn.close()


def bse_equity_init():
    _exec_script(DB_PATHS["bse_equity"], BSE_EQUITY_DDL)
    _bse_equity_migrate()
    log.info("[BSE_EQUITY] DB ready: %s", os.path.abspath(DB_PATHS["bse_equity"]))


def _get_or_create_category(conn, name: str) -> Optional[int]:
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO categories (name) VALUES (?)", (name,)).lastrowid


def _get_or_create_subcategory(conn, category_id: Optional[int], name: str) -> Optional[int]:
    name = (name or "").strip()
    if not name or category_id is None:
        return None
    row = conn.execute(
        "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
        (category_id, name),
    ).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
        (category_id, name),
    ).lastrowid


def bse_equity_store(rows: list) -> int:
    """
    Insert new announcements, and — this is the fix — actually UPDATE the
    category/subcategory of an existing announcement when BSE re-publishes
    the same (script_id, file_name, input_timestamp) with a corrected
    classification. The old code used INSERT OR IGNORE keyed on that same
    tuple, so once a row existed, any later change to SUBCATEGORYNAME for
    that identical announcement was silently dropped instead of applied.
    """
    changed = 0
    path = DB_PATHS["bse_equity"]
    with open_db(path) as conn:
        for r in rows:
            try:
                cat_id    = _get_or_create_category(conn, r.get("CATEGORYNAME", ""))
                subcat_id = _get_or_create_subcategory(conn, cat_id, r.get("SUBCATNAME", ""))

                script_id = str(r.get("SCRIP_CD", ""))
                file_name = r.get("ATTACHMENTNAME", "")
                news_dt   = r.get("NEWS_DT", "")
                attach_url = f"{BSE_EQ_ATTACH_BASE}{file_name}" if file_name else ""

                existing = conn.execute("""
                    SELECT id, category_id, subcategory_id FROM announcements
                    WHERE script_id = ? AND file_name = ? AND input_timestamp = ?
                """, (script_id, file_name, news_dt)).fetchone()

                if existing is None:
                    conn.execute("""
                        INSERT INTO announcements
                            (script_id, scrip_code, symbol, company_name, category_id,
                             subcategory_id, subject, file_name, input_timestamp,
                             attachment_url, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        script_id, script_id,
                        r.get("SLONGNAME") or r.get("NSURL") or "",
                        r.get("SLONGNAME", ""),
                        cat_id, subcat_id,
                        r.get("HEADLINE", ""),
                        file_name, news_dt, attach_url,
                        json.dumps(r, ensure_ascii=False),
                    ))
                    changed += 1
                elif existing["category_id"] != cat_id or existing["subcategory_id"] != subcat_id:
                    conn.execute("""
                        UPDATE announcements
                        SET category_id = ?, subcategory_id = ?
                        WHERE id = ?
                    """, (cat_id, subcat_id, existing["id"]))
                    changed += 1
            except sqlite3.IntegrityError:
                pass
    return changed


def bse_equity_backfill_subcategories() -> int:
    """One-time repair for rows ingested before the SUBCATNAME field-name
    fix. Those rows have subcategory_id = NULL even though BSE actually
    sent a subcategory — the raw response is still sitting in raw_json,
    so we re-derive it from there instead of re-fetching from BSE."""
    path = DB_PATHS["bse_equity"]
    fixed = 0
    with open_db(path) as conn:
        rows = conn.execute("""
            SELECT id, category_id, raw_json FROM announcements
            WHERE subcategory_id IS NULL AND raw_json IS NOT NULL AND raw_json != ''
        """).fetchall()
        for row in rows:
            if row["category_id"] is None:
                continue
            try:
                r = json.loads(row["raw_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            subcat_name = (r.get("SUBCATNAME") or "").strip()
            if not subcat_name:
                continue
            subcat_id = _get_or_create_subcategory(conn, row["category_id"], subcat_name)
            if subcat_id is not None:
                conn.execute(
                    "UPDATE announcements SET subcategory_id = ? WHERE id = ?",
                    (subcat_id, row["id"]),
                )
                fixed += 1
    return fixed


def bse_equity_backfill_attachment_urls() -> int:
    """One-time repair for rows where attachment_url was wrongly stored as
    the bare filename instead of the full document URL."""
    path = DB_PATHS["bse_equity"]
    with open_db(path) as conn:
        cur = conn.execute("""
            UPDATE announcements
            SET attachment_url = ? || file_name
            WHERE file_name IS NOT NULL AND file_name != ''
              AND (attachment_url IS NULL OR attachment_url = '' OR attachment_url = file_name)
        """, (BSE_EQ_ATTACH_BASE,))
        return cur.rowcount


def bse_equity_log_run_start() -> int:
    path = DB_PATHS["bse_equity"]
    with open_db(path) as conn:
        cur = conn.execute(
            "INSERT INTO run_log (started_at, status) VALUES (?, 'RUNNING')",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )
        return cur.lastrowid


def bse_equity_log_run_finish(run_id: int, status: str, fetched: int, inserted: int, error: str = ""):
    path = DB_PATHS["bse_equity"]
    with open_db(path) as conn:
        conn.execute("""
            UPDATE run_log
            SET finished_at=?, status=?, records_fetched=?, records_inserted=?, error_message=?
            WHERE id=?
        """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, fetched, inserted, error, run_id))


def bse_equity_query(from_date: str = "", to_date: str = "", symbol: str = "") -> list:
    """Return rows from bse_equity.db filtered by date/symbol."""
    bse_equity_init()  # idempotent: ensures schema/migration/view exist even if
                        # `query`/`report`/`export`/`stats` runs before `fetch` does
    clauses, params = [], []
    if from_date:
        clauses.append("input_timestamp >= ?")
        params.append(parse_ddmmyyyy(from_date).strftime("%Y-%m-%d"))
    if to_date:
        clauses.append("input_timestamp <= ?")
        params.append(parse_ddmmyyyy(to_date).strftime("%Y-%m-%d") + " 23:59:59")
    if symbol:
        clauses.append("LOWER(symbol) LIKE ?")
        params.append(f"%{symbol.lower()}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT scrip_code, symbol, company_name, category, subcategory, subject,
               input_timestamp, attachment_url
        FROM   v_announcements
        {where}
        ORDER  BY input_timestamp DESC
    """
    with open_db(DB_PATHS["bse_equity"]) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def bse_equity_stats() -> dict:
    bse_equity_init()  # idempotent, see note in bse_equity_query
    with open_db(DB_PATHS["bse_equity"]) as conn:
        total = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        cats  = conn.execute(
            "SELECT category, COUNT(*) n FROM v_announcements GROUP BY category ORDER BY n DESC LIMIT 10"
        ).fetchall()
        runs  = conn.execute(
            "SELECT started_at, status, records_fetched, records_inserted FROM run_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "total": total,
        "categories": [dict(r) for r in cats],
        "recent_runs": [dict(r) for r in runs],
    }


def _bse_equity_fetch_all_pages(bse_client, **kwargs) -> list:
    """Page through the BSE announcements API until exhausted."""
    all_rows, page_no = [], 1
    while True:
        data = bse_client.announcements(page_no=page_no, **kwargs)
        rows = data.get("Table", [])
        all_rows.extend(rows)
        total = data.get("Table1", [{}])[0].get("ROWCNT", len(all_rows))
        if not rows or len(all_rows) >= total:
            break
        page_no += 1
    return all_rows


def _bse_equity_fetch_with_retry(bse_client, **kwargs) -> list:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _bse_equity_fetch_all_pages(bse_client, **kwargs)
        except Exception as e:
            last_err = e
            log.warning("[BSE_EQUITY] Attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_err


def fetch_bse_equity(from_date: str, to_date: str, symbols: List[str] = None):
    """Main fetch entry-point for DB 1 (BSE Equity)."""
    try:
        from bse import BSE
    except ImportError:
        log.error("[BSE_EQUITY] 'bse' library not installed. Run: pip install bse")
        return 0, 0

    bse_equity_init()
    run_id       = bse_equity_log_run_start()
    total_f = total_i = 0

    from_dt = parse_ddmmyyyy(from_date)
    to_dt   = parse_ddmmyyyy(to_date)

    try:
        with BSE(download_folder="./") as bse:
            if symbols:
                log.info("[BSE_EQUITY] Watchlist mode: %s  %s → %s", symbols, from_date, to_date)
                for sym in symbols:
                    code = bse.getScripCode(sym)
                    if not code:
                        log.warning("[BSE_EQUITY] Could not resolve '%s', skipping.", sym)
                        continue
                    rows = _bse_equity_fetch_with_retry(
                        bse, segment="equity",
                        from_date=from_dt, to_date=to_dt,
                        category="-1", subcategory="-1", scripcode=code,
                    )
                    ins = bse_equity_store(rows)
                    total_f += len(rows); total_i += ins
                    log.info("[BSE_EQUITY] %s (%s): fetched %d, inserted %d", sym, code, len(rows), ins)
            else:
                log.info("[BSE_EQUITY] Market-wide mode: %s → %s", from_date, to_date)
                # Market-wide (no scripcode) announcements can run to 2000+
                # rows / 50+ pages for a SINGLE day. Fetching a multi-day
                # range in one shot risks a mid-pagination timeout/rate-limit
                # wiping out everything fetched so far (nothing gets stored
                # until the whole range succeeds). So we always chunk
                # day-by-day and store after each day — a bad day only
                # costs that day, not the whole range.
                day_pairs = list(day_chunks(from_date, to_date))
                if len(day_pairs) > 1:
                    log.info("[BSE_EQUITY] Splitting into %d daily chunks", len(day_pairs))
                for i, (df, dt) in enumerate(day_pairs, 1):
                    df_dt = parse_ddmmyyyy(df)
                    dt_dt = parse_ddmmyyyy(dt)
                    try:
                        rows = _bse_equity_fetch_with_retry(
                            bse, segment="equity",
                            from_date=df_dt, to_date=dt_dt,
                            category="-1", subcategory="-1", scripcode=None,
                        )
                        ins = bse_equity_store(rows)
                        total_f += len(rows); total_i += ins
                        log.info("[BSE_EQUITY] %s: fetched %d, inserted %d", df, len(rows), ins)
                    except Exception as e:
                        log.error("[BSE_EQUITY] %s: FAILED — %s (skipping day, keeping prior progress)", df, e)
                    if i < len(day_pairs):
                        time.sleep(1)
                log.info("[BSE_EQUITY] ALL: fetched %d, inserted %d", total_f, total_i)

        bse_equity_log_run_finish(run_id, "SUCCESS", total_f, total_i)

    except Exception as e:
        bse_equity_log_run_finish(run_id, "FAILED", total_f, total_i, str(e))
        log.error("[BSE_EQUITY] Run FAILED: %s", e)
        log.debug(traceback.format_exc())

    return total_f, total_i


# ─────────────────────────────────────────────────────────────────────────────
#  ████████████████████████████████████████████████████████████████████████████
#  DB 2 — BSE SME  (bse_sme.db)
#  Scrapes www.bsesme.com HTML pages — announcements + corporate actions.
#  ████████████████████████████████████████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

BSE_SME_ANN_URL  = "https://www.bsesme.com/corpoaratefilings/Announcements.aspx?expandable=0"
BSE_SME_CORP_URL = "https://www.bsesme.com/corpoaratefilings/Corp_Actions.aspx?expandable=0"
BSE_SME_BASE_URL = "https://www.bsesme.com"

BSE_SME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                    "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language":           "en-IN,en;q=0.5",
    "Accept-Encoding":           "gzip, deflate, br",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

BSE_SME_DDL = """
CREATE TABLE IF NOT EXISTS announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scrip_code      TEXT,
    scrip_name      TEXT NOT NULL,
    grp             TEXT,
    category        TEXT,
    announce_date   TEXT,
    end_date        TEXT,
    purpose         TEXT,
    attachment_url  TEXT,
    ann_time        TEXT,
    full_text_link  TEXT,
    summary         TEXT,
    fetched_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (scrip_code, announce_date, purpose)
);
CREATE INDEX IF NOT EXISTS idx_bsesme_ann_scrip    ON announcements(scrip_name);
CREATE INDEX IF NOT EXISTS idx_bsesme_ann_date     ON announcements(announce_date);
CREATE INDEX IF NOT EXISTS idx_bsesme_ann_category ON announcements(category);
CREATE INDEX IF NOT EXISTS idx_bsesme_ann_grp      ON announcements(grp);
CREATE TABLE IF NOT EXISTS corp_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scrip_code   TEXT,
    scrip_name   TEXT NOT NULL,
    grp          TEXT,
    category     TEXT,
    ex_date      TEXT,
    record_date  TEXT,
    end_date     TEXT,
    purpose      TEXT,
    fetched_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (scrip_code, ex_date, purpose)
);
CREATE INDEX IF NOT EXISTS idx_bsesme_corp_scrip    ON corp_actions(scrip_name);
CREATE INDEX IF NOT EXISTS idx_bsesme_corp_ex_date  ON corp_actions(ex_date);
CREATE INDEX IF NOT EXISTS idx_bsesme_corp_category ON corp_actions(category);
CREATE INDEX IF NOT EXISTS idx_bsesme_corp_grp      ON corp_actions(grp);
CREATE TABLE IF NOT EXISTS fetch_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    rows_found   INTEGER DEFAULT 0,
    rows_new     INTEGER DEFAULT 0,
    status       TEXT,
    error_msg    TEXT
);
"""


def _bse_sme_migrate():
    """
    Add any columns that older bse_sme.db files (created before the
    ann_time / full_text_link / summary fields existed) are missing.
    CREATE TABLE IF NOT EXISTS does not retrofit existing tables, so this
    runs a lightweight ALTER TABLE pass each time the DB is opened.
    """
    path = DB_PATHS["bse_sme"]
    required_ann_cols = {
        "grp": "TEXT", "category": "TEXT", "announce_date": "TEXT",
        "end_date": "TEXT", "purpose": "TEXT", "attachment_url": "TEXT",
        "ann_time": "TEXT", "full_text_link": "TEXT", "summary": "TEXT",
    }
    conn = sqlite3.connect(path)
    try:
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "announcements" in existing_tables:
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(announcements)").fetchall()}
            for col, coltype in required_ann_cols.items():
                if col not in existing_cols:
                    log.info("[BSE_SME] Migrating DB: adding column announcements.%s", col)
                    conn.execute(f"ALTER TABLE announcements ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        conn.close()


def bse_sme_init():
    _exec_script(DB_PATHS["bse_sme"], BSE_SME_DDL)
    _bse_sme_migrate()
    log.info("[BSE_SME] DB ready: %s", os.path.abspath(DB_PATHS["bse_sme"]))


def _bse_sme_log_fetch(source, started, finished, found, new, status, error=""):
    path = DB_PATHS["bse_sme"]
    with open_db(path) as conn:
        conn.execute("""
            INSERT INTO fetch_log
                (source, started_at, finished_at, rows_found, rows_new, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, started, finished, found, new, status, error))


def _bse_sme_upsert_ann(rows: list) -> int:
    count = 0
    with open_db(DB_PATHS["bse_sme"]) as conn:
        for r in rows:
            r.setdefault("ann_time", "")
            r.setdefault("full_text_link", "")
            r.setdefault("summary", "")
            cur = conn.execute("""
                INSERT OR IGNORE INTO announcements
                    (scrip_code, scrip_name, grp, category,
                     announce_date, end_date, purpose, attachment_url,
                     ann_time, full_text_link, summary)
                VALUES (:scrip_code, :scrip_name, :grp, :category,
                        :announce_date, :end_date, :purpose, :attachment_url,
                        :ann_time, :full_text_link, :summary)
            """, r)
            count += cur.rowcount
    return count


def _bse_sme_upsert_corp(rows: list) -> int:
    count = 0
    with open_db(DB_PATHS["bse_sme"]) as conn:
        for r in rows:
            cur = conn.execute("""
                INSERT OR IGNORE INTO corp_actions
                    (scrip_code, scrip_name, grp, category,
                     ex_date, record_date, end_date, purpose)
                VALUES (:scrip_code, :scrip_name, :grp, :category,
                        :ex_date, :record_date, :end_date, :purpose)
            """, r)
            count += cur.rowcount
    return count


def bse_sme_query_ann(scrip="", category="", grp="", from_dt="", to_dt="") -> list:
    clauses, params = [], []
    if scrip:    clauses.append("LOWER(scrip_name) LIKE ?"); params.append(f"%{scrip.lower()}%")
    if category: clauses.append("LOWER(category) LIKE ?");  params.append(f"%{category.lower()}%")
    if grp:      clauses.append("grp = ?");                 params.append(grp.upper())
    if from_dt:  clauses.append("announce_date >= ?");      params.append(from_dt)
    if to_dt:    clauses.append("announce_date <= ?");       params.append(to_dt)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, scrip_code, scrip_name, grp, category,
               announce_date, end_date, purpose, attachment_url,
               ann_time, full_text_link, summary, fetched_at
        FROM   announcements {where}
        ORDER  BY announce_date DESC, id DESC
    """
    with open_db(DB_PATHS["bse_sme"]) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def bse_sme_query_corp(scrip="", category="", grp="", from_dt="", to_dt="") -> list:
    clauses, params = [], []
    if scrip:    clauses.append("LOWER(scrip_name) LIKE ?"); params.append(f"%{scrip.lower()}%")
    if category: clauses.append("LOWER(category) LIKE ?");  params.append(f"%{category.lower()}%")
    if grp:      clauses.append("grp = ?");                 params.append(grp.upper())
    if from_dt:  clauses.append("ex_date >= ?");            params.append(from_dt)
    if to_dt:    clauses.append("ex_date <= ?");             params.append(to_dt)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, scrip_code, scrip_name, grp, category,
               ex_date, record_date, end_date, purpose, fetched_at
        FROM   corp_actions {where}
        ORDER  BY ex_date DESC, id DESC
    """
    with open_db(DB_PATHS["bse_sme"]) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def bse_sme_stats() -> dict:
    path = DB_PATHS["bse_sme"]
    with open_db(path) as conn:
        ann_total  = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        corp_total = conn.execute("SELECT COUNT(*) FROM corp_actions").fetchone()[0]
        ann_cats   = conn.execute(
            "SELECT category, COUNT(*) n FROM announcements GROUP BY category ORDER BY n DESC LIMIT 10"
        ).fetchall()
        corp_cats  = conn.execute(
            "SELECT category, COUNT(*) n FROM corp_actions GROUP BY category ORDER BY n DESC LIMIT 10"
        ).fetchall()
        last_fetch = conn.execute(
            "SELECT source, finished_at, rows_new, status FROM fetch_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "ann_total":  ann_total,
        "corp_total": corp_total,
        "ann_cats":   [dict(r) for r in ann_cats],
        "corp_cats":  [dict(r) for r in corp_cats],
        "last_fetch": [dict(r) for r in last_fetch],
    }


# ── BSE SME HTTP session (ASP.NET ViewState) ───────────────────────────────

class _BSESMESession:
    ASPNET_FIELDS = ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]

    def __init__(self):
        try:
            import requests as req
        except ImportError:
            raise SystemExit("[BSE_SME] 'requests' not installed. Run: pip install requests")
        self._req     = req
        self.session  = req.Session()
        self.session.headers.update(BSE_SME_HEADERS)
        self._vs: Dict[str, str] = {f: "" for f in self.ASPNET_FIELDS}

    def _grab_vs(self, soup):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise SystemExit("[BSE_SME] 'beautifulsoup4' not installed. Run: pip install beautifulsoup4 lxml")
        for field in self.ASPNET_FIELDS:
            tag = soup.find("input", {"name": field})
            self._vs[field] = tag.get("value", "") if tag else ""

    def _request(self, method, url, **kwargs):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, timeout=BSE_SME_REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = BSE_SME_RETRY_BACKOFF * attempt
                log.warning("[BSE_SME] Attempt %d/%d failed (%s) – retrying in %ds",
                            attempt, MAX_RETRIES, exc, wait)
                time.sleep(wait)

    def _parse(self, resp):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        self._grab_vs(soup)
        return soup

    def get(self, url, **kw):
        return self._parse(self._request("GET", url, **kw))

    def post(self, url, extra=None):
        payload = dict(self._vs)
        if extra:
            payload.update(extra)
        return self._parse(self._request("POST", url, data=payload))

    def seed(self, url):
        return self.get(url)


# ── BSE SME HTML parsing ───────────────────────────────────────────────────

def _sme_text(tag) -> str:
    return tag.get_text(separator=" ", strip=True) if tag else ""

def _sme_href(td) -> str:
    if td:
        a = td.find("a", href=True)
        if a:
            href = a["href"].strip()
            if href.startswith("http"):
                return href
            return BSE_SME_BASE_URL.rstrip("/") + "/" + href.lstrip("/")
    return ""

def _sme_find_table(soup):
    for pattern in (r"GridView", r"gvData", r"gvAnnounce",
                    r"dgAnnounce", r"gvActions", r"dgActions", r"GridView1"):
        tbl = soup.find("table", {"id": re.compile(pattern, re.I)})
        if tbl:
            return tbl
    tables = soup.find_all("table")
    return max(tables, key=lambda t: len(t.find_all("tr"))) if tables else None

def _sme_col_idx(headers, kws, default):
    for i, h in enumerate(headers):
        if any(k in h.lower() for k in kws):
            return i
    return default

def _sme_parse_table(soup):
    table = _sme_find_table(soup)
    if not table:
        log.warning("[BSE_SME] No data table found on page")
        return [], []
    all_trs = table.find_all("tr")
    if not all_trs:
        return [], []
    headers  = [_sme_text(c) for c in all_trs[0].find_all(["th", "td"])]
    data_rows = [tr.find_all(["td", "th"]) for tr in all_trs[1:]
                 if len(tr.find_all(["td", "th"])) >= 2]
    return headers, data_rows

def _sme_split_title(raw: str) -> tuple:
    """
    Given a string like  "RECODE - 544755 - Announcement under ..."
    return  ("RECODE", "544755", "Announcement under ...")
    """
    parts = raw.split(" - ", 2)
    if len(parts) == 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), ""
    return "", "", raw.strip()


def _sme_parse_announcements(soup) -> list:
    """
    Robust row-by-row parser for the BSE SME announcements page
    (ported from bse_sme_scraper.py — works on the live divCorp structure).
    """
    results: list = []
    current_date = ""

    div = soup.find("div", {"id": "ContentPlaceHolder1_divCorp"})
    if div is None:
        div = soup  # fall back to scanning the whole document

    rows = div.find_all("tr")
    i = 0
    while i < len(rows):
        cells = rows[i].find_all("td")
        if not cells:
            i += 1
            continue

        first_cls = cells[0].get("class", [])

        # ── Section date header ────────────────────────────────────────
        if "announceheader" in first_cls:
            current_date = cells[0].get_text(strip=True)
            i += 1
            continue

        # ── Announcement header row ──────────────────────────────────
        if "TTHeadergrey" in first_cls:
            raw_title = cells[0].get_text(" ", strip=True)

            a_red = cells[0].find("a", class_="tablebluelink")
            full_link = a_red["href"] if a_red else ""

            scrip_name, scrip_code, ann_title = _sme_split_title(raw_title)

            category = ""
            pdf_url  = ""
            ann_time = ""

            if len(cells) >= 2:
                category = cells[1].get_text(strip=True)
            if len(cells) >= 3:
                a = cells[2].find("a")
                pdf_url = a["href"] if a else ""
            if len(cells) >= 4:
                ann_time = cells[3].get_text(strip=True)

            # Collect all immediately following summary rows
            summary_parts = []
            i += 1
            while i < len(rows):
                nc = rows[i].find_all("td")
                if nc and "TTRow_leftnotices" in nc[0].get("class", []):
                    t = nc[0].get_text(" ", strip=True)
                    if t and t != "\xa0":
                        summary_parts.append(t)
                    i += 1
                else:
                    break

            summary = " ".join(summary_parts).strip()

            if not scrip_name:
                continue

            results.append({
                "scrip_code":     scrip_code,
                "scrip_name":     scrip_name,
                "grp":            "",
                "category":       category,
                "announce_date":  to_iso(current_date),
                "end_date":       "",
                "purpose":        ann_title or category,
                "attachment_url": pdf_url,
                "ann_time":       ann_time,
                "full_text_link": full_link,
                "summary":        summary,
            })
            continue  # i already advanced inside the inner loop

        i += 1

    return results

def _sme_parse_corp_actions(soup) -> list:
    headers, rows = _sme_parse_table(soup)
    if not rows:
        return []
    i_code  = _sme_col_idx(headers, ["scrip code", "scripcode", "code"],       0)
    i_name  = _sme_col_idx(headers, ["scrip name", "company", "name"],          1)
    i_grp   = _sme_col_idx(headers, ["group"],                                   2)
    i_cat   = _sme_col_idx(headers, ["purpose", "category", "action", "type"],  3)
    date_indices = [i for i, h in enumerate(headers) if any(k in h.lower() for k in ["date", "dt"])]
    i_ex  = date_indices[0] if len(date_indices) > 0 else 4
    i_rec = date_indices[1] if len(date_indices) > 1 else -1
    i_end = date_indices[2] if len(date_indices) > 2 else -1
    results = []
    for cells in rows:
        def c(idx):
            return _sme_text(cells[idx]) if 0 <= idx < len(cells) else ""
        scrip_name = c(i_name)
        if not scrip_name:
            continue
        results.append({
            "scrip_code":  c(i_code),
            "scrip_name":  scrip_name,
            "grp":         c(i_grp),
            "category":    c(i_cat),
            "ex_date":     to_iso(c(i_ex)),
            "record_date": to_iso(c(i_rec)),
            "end_date":    to_iso(c(i_end)),
            "purpose":     c(i_cat),
        })
    return results

def _sme_active_page(soup) -> int:
    for span in soup.find_all("span"):
        txt = span.get_text(strip=True)
        if txt.isdigit() and span.parent and span.parent.name == "td":
            return int(txt)
    return 1

def _sme_pager_targets(soup) -> list:
    targets, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"__doPostBack", re.I)):
        m = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", a.get("href", ""))
        if m:
            key = (m.group(1), m.group(2))
            if key not in seen:
                seen.add(key)
                targets.append({"target": m.group(1), "argument": m.group(2)})
    return targets

def _sme_next_page(soup, visited) -> Optional[dict]:
    for t in _sme_pager_targets(soup):
        m = re.match(r"Page\$(\d+)", t["argument"])
        if m and int(m.group(1)) not in visited:
            return t
    return None


def _bse_sme_fetch_announcements(from_date: str = "", to_date: str = "") -> int:
    """
    Fetch BSE SME announcements for the given DD/MM/YYYY date range.
    Ported from bse_sme_scraper.py: uses the correct ASP.NET control names
    (ddlScrip / ddlCatName / ddlGroup / txtToDate / txtFrmDate / btnSubmit)
    and a single POST (the result div contains the full date-range listing,
    no separate pager step is required).
    """
    source  = "announcements"
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[BSE_SME] Announcements fetch started")
    all_rows = []
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        raise SystemExit("[BSE_SME] 'requests'/'beautifulsoup4' not installed. "
                          "Run: pip install requests beautifulsoup4 lxml")
    try:
        session = requests.Session()
        resp = session.get(BSE_SME_ANN_URL, headers=BSE_SME_HEADERS, timeout=BSE_SME_REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def _val(name: str) -> str:
            tag = soup.find("input", {"name": name})
            return tag["value"] if tag else ""

        payload = {
            "__VIEWSTATE":             _val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR":    _val("__VIEWSTATEGENERATOR"),
            "__VIEWSTATEENCRYPTED":    _val("__VIEWSTATEENCRYPTED"),
            "__EVENTVALIDATION":       _val("__EVENTVALIDATION"),
            "ctl00$ContentPlaceHolder1$type":       "rdnPeriod",
            "ctl00$ContentPlaceHolder1$hdnVal":      "A",
            "ctl00$ContentPlaceHolder1$hdnCat":      "",
            "ctl00$ContentPlaceHolder1$ddlScrip":    "Select",
            "ctl00$ContentPlaceHolder1$ddlCatName":  "-1",
            "ctl00$ContentPlaceHolder1$ddlGroup":    "",
            "ctl00$ContentPlaceHolder1$txtToDate":   from_date,   # site's "Date" field
            "ctl00$ContentPlaceHolder1$txtFrmDate":  to_date,     # site's "End Date" field
            "ctl00$ContentPlaceHolder1$btnSubmit.x": "1",
            "ctl00$ContentPlaceHolder1$btnSubmit.y": "1",
        }
        if from_date or to_date:
            log.info("[BSE_SME] Date filter: %s to %s", from_date or "default", to_date or "default")

        resp2 = session.post(BSE_SME_ANN_URL, data=payload, headers=BSE_SME_HEADERS, timeout=BSE_SME_REQUEST_TIMEOUT)
        resp2.raise_for_status()
        result_soup = BeautifulSoup(resp2.text, "html.parser")

        all_rows = _sme_parse_announcements(result_soup)
        log.info("[BSE_SME] Announcements: %d rows parsed", len(all_rows))

        new = _bse_sme_upsert_ann(all_rows)
        log.info("[BSE_SME] Announcements: %d fetched, %d new", len(all_rows), new)
        _bse_sme_log_fetch(source, started, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(all_rows), new, "ok")
        return new
    except Exception as exc:
        log.error("[BSE_SME] Announcements fetch error: %s", exc)
        _bse_sme_log_fetch(source, started, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(all_rows), 0, "error", str(exc))
        return 0


def _bse_sme_fetch_corp_actions() -> int:
    source   = "corp_actions"
    started  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session  = _BSESMESession()
    all_rows = []
    log.info("[BSE_SME] Corp actions fetch started")
    try:
        soup    = session.seed(BSE_SME_CORP_URL)
        visited = set()
        for _ in range(BSE_SME_MAX_PAGES):
            cur = _sme_active_page(soup)
            if cur in visited:
                break
            visited.add(cur)
            page_rows = _sme_parse_corp_actions(soup)
            log.info("[BSE_SME]  CORP page %d → %d rows", cur, len(page_rows))
            all_rows.extend(page_rows)
            nxt = _sme_next_page(soup, visited)
            if not nxt:
                break
            soup = session.post(BSE_SME_CORP_URL, {"__EVENTTARGET": nxt["target"], "__EVENTARGUMENT": nxt["argument"]})
            time.sleep(BSE_SME_PAGE_SLEEP)
        new = _bse_sme_upsert_corp(all_rows)
        log.info("[BSE_SME] Corp actions: %d fetched, %d new", len(all_rows), new)
        _bse_sme_log_fetch(source, started, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(all_rows), new, "ok")
        return new
    except Exception as exc:
        log.error("[BSE_SME] Corp actions fetch error: %s", exc)
        _bse_sme_log_fetch(source, started, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(all_rows), 0, "error", str(exc))
        return 0


def fetch_bse_sme(from_date: str = "", to_date: str = "", do_ann: bool = True, do_corp: bool = True):
    """Main fetch entry-point for DB 2 (BSE SME)."""
    # Convert DD-MM-YYYY → DD/MM/YYYY for bsesme.com filter boxes
    def _conv(d):
        try:
            return parse_ddmmyyyy(d).strftime("%d/%m/%Y")
        except Exception:
            return d

    fd = _conv(from_date) if from_date else ""
    td = _conv(to_date)   if to_date   else ""

    bse_sme_init()
    total = 0
    if do_ann:
        total += _bse_sme_fetch_announcements(fd, td)
    if do_corp:
        total += _bse_sme_fetch_corp_actions()
    return total


# ─────────────────────────────────────────────────────────────────────────────
#  ████████████████████████████████████████████████████████████████████████████
#  DB 3 — NSE EQUITY  (nse_equity.db)
#  DB 4 — NSE SME     (nse_sme.db)
#  Both use the same NSE API; only the `index` parameter differs.
#  ████████████████████████████████████████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

NSE_BASE_URL = "https://www.nseindia.com"
NSE_API_URL  = "https://www.nseindia.com/api/corporate-announcements"

NSE_HEADERS = {
    "Accept":             "*/*",
    "Accept-Language":    "en-US,en;q=0.9",
    "Referer":            "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    "sec-ch-ua":          '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-origin",
}

# NSE index tag → our source key
NSE_INDEX_MAP = {
    "nse_equity": "equities",
    "nse_sme":    "sme",
}

NSE_SHARED_DDL = """
CREATE TABLE IF NOT EXISTS announcements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ann_id         TEXT    UNIQUE,
    symbol         TEXT,
    company_name   TEXT,
    subject        TEXT,
    description    TEXT,
    ann_date       TEXT,
    attachment_url TEXT,
    fetched_at     TEXT    DEFAULT (datetime('now','localtime')),
    raw_json       TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbol   ON announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(ann_date);
"""


def nse_init(source: str):
    """Initialise DB for nse_equity or nse_sme."""
    _exec_script(DB_PATHS[source], NSE_SHARED_DDL)
    log.info("[%s] DB ready: %s", source.upper(), os.path.abspath(DB_PATHS[source]))


def nse_store(rows: list, source: str) -> tuple:
    """Insert NSE rows into the appropriate DB; return (inserted, skipped)."""
    inserted = skipped = 0
    index_tag = NSE_INDEX_MAP[source]
    with open_db(DB_PATHS[source]) as conn:
        for a in rows:
            ann_id = (
                a.get("an_un_id")
                or a.get("ann_id")
                or f"{index_tag}_{a.get('symbol','')}_{a.get('bfDissTime', a.get('an_dt',''))}"
            )
            try:
                conn.execute("""
                    INSERT INTO announcements
                        (ann_id, symbol, company_name, subject, description,
                         ann_date, attachment_url, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ann_id,
                    a.get("symbol"),
                    a.get("sm_name"),
                    a.get("subject", a.get("desc")),
                    a.get("attchmntText"),
                    a.get("bfDissTime", a.get("an_dt")),
                    (NSE_BASE_URL + a["attchmntFile"]) if a.get("attchmntFile") else None,
                    json.dumps(a, ensure_ascii=False),
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return inserted, skipped


def nse_query(source: str, from_date: str = "", to_date: str = "") -> list:
    def _iso(d):
        return parse_ddmmyyyy(d).strftime("%Y-%m-%d")
    clauses, params = [], []
    if from_date:
        clauses.append("""
            substr(ann_date,7,4)||'-'||substr(ann_date,4,2)||'-'||substr(ann_date,1,2) >= ?
        """)
        params.append(_iso(from_date))
    if to_date:
        clauses.append("""
            substr(ann_date,7,4)||'-'||substr(ann_date,4,2)||'-'||substr(ann_date,1,2) <= ?
        """)
        params.append(_iso(to_date))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT symbol, company_name, subject, ann_date, attachment_url
        FROM   announcements {where}
        ORDER  BY ann_date DESC
    """
    with open_db(DB_PATHS[source]) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def nse_stats(source: str) -> dict:
    with open_db(DB_PATHS[source]) as conn:
        total = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        by_sym = conn.execute(
            "SELECT symbol, COUNT(*) n FROM announcements GROUP BY symbol ORDER BY n DESC LIMIT 10"
        ).fetchall()
    return {"total": total, "top_symbols": [dict(r) for r in by_sym]}


def _nse_create_session():
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        raise SystemExit("[NSE] 'curl_cffi' not installed. Run: pip install curl_cffi")
    session = curl_requests.Session(impersonate="chrome124")
    log.info("[NSE] Visiting NSE homepage ...")
    r = session.get(NSE_BASE_URL, headers=NSE_HEADERS, timeout=30)
    log.info("[NSE] Homepage → HTTP %d | Cookies: %s", r.status_code, list(session.cookies.keys()))
    if r.status_code != 200:
        raise RuntimeError(f"[NSE] Homepage blocked HTTP {r.status_code}")
    time.sleep(2)
    r = session.get(
        f"{NSE_BASE_URL}/companies-listing/corporate-filings-announcements",
        headers=NSE_HEADERS, timeout=30,
    )
    log.info("[NSE] Filings page → HTTP %d", r.status_code)
    time.sleep(1)
    return session


def _nse_fetch_one_chunk(session, from_date: str, to_date: str, index_tag: str) -> list:
    params = {"index": index_tag, "from_date": from_date, "to_date": to_date, "reqXbrl": "false"}
    r = session.get(NSE_API_URL, headers=NSE_HEADERS, params=params, timeout=60)
    log.info("[NSE] [%s] %s→%s → HTTP %d", index_tag.upper(), from_date, to_date, r.status_code)
    if r.status_code in (401, 403):
        raise RuntimeError(f"[NSE] HTTP {r.status_code} — session rejected.")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "announcements"):
            if key in data and isinstance(data[key], list):
                return data[key]
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def _nse_fetch_index(from_date: str, to_date: str, source: str) -> list:
    """Fetch all data for one NSE source, chunking if date range > 30 days."""
    index_tag = NSE_INDEX_MAP[source]
    if not needs_chunking(from_date, to_date):
        session = _nse_create_session()
        return _nse_fetch_one_chunk(session, from_date, to_date, index_tag)

    chunks  = list(month_chunks(from_date, to_date))
    all_rows = []
    session  = _nse_create_session()
    log.info("[%s] Splitting into %d monthly chunks", source.upper(), len(chunks))

    for i, (cf, ct) in enumerate(chunks, 1):
        if i > 1 and (i - 1) % 3 == 0:
            log.info("[%s] Refreshing NSE session ...", source.upper())
            session = _nse_create_session()
        try:
            rows = _nse_fetch_one_chunk(session, cf, ct, index_tag)
            log.info("[%s] Chunk %d/%d: %d records", source.upper(), i, len(chunks), len(rows))
            all_rows.extend(rows)
        except Exception as e:
            log.error("[%s] Chunk %d failed: %s — skipping", source.upper(), i, e)
        if i < len(chunks):
            time.sleep(2)
    return all_rows


def fetch_nse(source: str, from_date: str, to_date: str):
    """Main fetch entry-point for DB 3 (nse_equity) or DB 4 (nse_sme)."""
    nse_init(source)
    label = source.upper()
    log.info("[%s] Fetch: %s → %s", label, from_date, to_date)
    total_f = total_i = 0
    try:
        rows = _nse_fetch_index(from_date, to_date, source)
        ins, skipped = nse_store(rows, source)
        total_f = len(rows)
        total_i = ins
        log.info("[%s] fetched %d, inserted %d, skipped %d", label, len(rows), ins, skipped)
    except Exception as e:
        log.error("[%s] Fetch FAILED: %s", label, e)
        log.debug(traceback.format_exc())
    return total_f, total_i


# ─────────────────────────────────────────────────────────────────────────────
#  REPORT GENERATOR  (works for any source)
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(source: str, from_date: str, to_date: str) -> Path:
    """Generate a formatted plain-text report from stored data."""
    rdir  = Path(REPORTS_DIR)
    rdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = rdir / f"{source}_{from_date.replace('-','')}_{to_date.replace('-','')}_{stamp}.txt"
    sep   = "=" * 72
    dash  = "-" * 72

    # Pull rows from the right DB
    if source == "bse_equity":
        rows = bse_equity_query(from_date, to_date)
        cols = ("scrip_code", "symbol", "company_name", "category", "subcategory", "subject", "input_timestamp", "attachment_url")
    elif source == "bse_sme":
        rows = bse_sme_query_ann(from_dt=_ddmmyyyy_to_iso_prefix(from_date), to_dt=_ddmmyyyy_to_iso_prefix(to_date))
        cols = ("scrip_name", "scrip_code", "grp", "category", "announce_date", "ann_time", "purpose", "summary", "attachment_url")
    else:
        rows = nse_query(source, from_date, to_date)
        cols = ("symbol", "company_name", "ann_date", "subject", "attachment_url")

    lines = [
        sep,
        f"  {source.upper().replace('_', ' ')} — CORPORATE ANNOUNCEMENTS REPORT",
        f"  Period   : {from_date}  to  {to_date}",
        f"  Total    : {len(rows)} announcements",
        f"  Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
        f"  Database : {DB_PATHS[source]}",
        sep, "",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(f"[{i:04d}]")
        for col in cols:
            val = r.get(col, "")
            if val:
                lines.append(f"        {col:<20}: {val}")
        lines.append(dash)

    lines += ["", f"  END OF REPORT — {source.upper()} — {len(rows)} records", sep]
    fname.write_text("\n".join(lines), encoding="utf-8")
    log.info("[%s] Report saved: %s", source.upper(), fname)
    return fname


def _ddmmyyyy_to_iso_prefix(d: str) -> str:
    """Convert DD-MM-YYYY → YYYY-MM-DD for BSE SME ISO date comparisons."""
    try:
        return parse_ddmmyyyy(d).strftime("%Y-%m-%d")
    except Exception:
        return d


# ─────────────────────────────────────────────────────────────────────────────
#  CLI COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_dates(args):
    today     = datetime.today()
    yesterday = today - timedelta(days=1)

    # --yesterday flag: override both ends to yesterday only
    if getattr(args, "yesterday", False):
        return yesterday.strftime("%d-%m-%Y"), yesterday.strftime("%d-%m-%Y")

    # Default window: today only  (no flags given)
    # A bare `python market_announcements.py fetch` captures today's announcements.
    # Use --yesterday to fetch the previous day instead.
    fd = args.from_date or today.strftime("%d-%m-%Y")
    td = args.to_date   or today.strftime("%d-%m-%Y")
    return fd, td

def _resolve_sources(args) -> list:
    s = getattr(args, "source", "all")
    return ALL_SOURCES if s == "all" else [s]


def cmd_fetch(args):
    from_date, to_date = _resolve_dates(args)
    sources = _resolve_sources(args)
    symbols = getattr(args, "symbols", None) or []

    print(f"\n{'='*70}")
    print(f"  FETCH  {from_date} → {to_date}  |  sources: {', '.join(sources)}")
    print(f"{'='*70}\n")

    grand_f = grand_i = 0
    for src in sources:
        print(f"  ▶  [{src.upper()}]")
        if src == "bse_equity":
            f, i = fetch_bse_equity(from_date, to_date, symbols or None)
        elif src == "bse_sme":
            f, i = fetch_bse_sme(from_date, to_date), 0
            i = f  # bse_sme returns new rows directly
        elif src in ("nse_equity", "nse_sme"):
            f, i = fetch_nse(src, from_date, to_date)
        else:
            print(f"     Unknown source: {src}")
            continue
        print(f"     Fetched: {f}  |  New inserted: {i}  |  DB: {DB_PATHS[src]}")
        grand_f += f; grand_i += i
        if src != sources[-1]:
            time.sleep(2)

    print(f"\n{'='*70}")
    print(f"  TOTAL  Fetched: {grand_f}  |  New inserted: {grand_i}")
    print(f"{'='*70}\n")


def cmd_query(args):
    from_date, to_date = _resolve_dates(args)
    sources = _resolve_sources(args)
    scrip   = getattr(args, "scrip", "") or ""

    for src in sources:
        print(f"\n{'='*70}")
        print(f"  [{src.upper()}]  {from_date} → {to_date}  |  DB: {DB_PATHS[src]}")
        print(f"{'='*70}\n")

        if src == "bse_equity":
            rows = bse_equity_query(from_date, to_date, scrip)
            for i, r in enumerate(rows, 1):
                print(f"  [{i:04d}] {r.get('symbol','N/A'):<14} {r.get('input_timestamp','')}")
                print(f"         Company     : {r.get('company_name','')}")
                print(f"         Category    : {r.get('category','')}")
                print(f"         Subcategory : {r.get('subcategory','')}")
                print(f"         Subject     : {r.get('subject','')}")
                if r.get("attachment_url"):
                    print(f"         PDF         : {r['attachment_url']}")
                print()
        elif src == "bse_sme":
            fd_iso = _ddmmyyyy_to_iso_prefix(from_date)
            td_iso = _ddmmyyyy_to_iso_prefix(to_date)
            ann  = bse_sme_query_ann(scrip=scrip, from_dt=fd_iso, to_dt=td_iso)
            corp = bse_sme_query_corp(scrip=scrip, from_dt=fd_iso, to_dt=td_iso)
            print(f"  ANNOUNCEMENTS ({len(ann)} rows)")
            for r in ann:
                print(f"    {r['scrip_name']:<20} {r['announce_date']}  {r['purpose']}")
            print(f"\n  CORP ACTIONS ({len(corp)} rows)")
            for r in corp:
                print(f"    {r['scrip_name']:<20} ex:{r['ex_date']}  {r['category']}")
            rows = ann  # for JSON export
        else:
            rows = nse_query(src, from_date, to_date)
            for i, r in enumerate(rows, 1):
                print(f"  [{i:04d}] {r.get('symbol','N/A'):<14} {r.get('ann_date','')}")
                print(f"         Company : {r.get('company_name','')}")
                print(f"         Subject : {r.get('subject','')}")
                if r.get("attachment_url"):
                    print(f"         PDF     : {r['attachment_url']}")
                print()

        output = getattr(args, "output", None)
        if output:
            suffix  = f"_{src}" if len(sources) > 1 else ""
            out_p   = Path(output)
            out_f   = out_p.with_stem(out_p.stem + suffix)
            with open(out_f, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=2, ensure_ascii=False)
            print(f"  Exported {len(rows)} records → {out_f}")


def cmd_report(args):
    from_date, to_date = _resolve_dates(args)
    sources = _resolve_sources(args)
    print()
    for src in sources:
        rpt = generate_report(src, from_date, to_date)
        print(f"  📄  [{src.upper()}] → {rpt}")
    print(f"\n  Reports saved in: {Path(REPORTS_DIR).resolve()}\n")


def cmd_backfill(args):
    """One-time repair for existing BSE Equity rows affected by the
    SUBCATNAME field-name bug and the attachment_url bug (fixed
    2026-07-10). Safe to re-run; only touches rows that still need it."""
    bse_equity_init()
    print("\n  🔧  Backfilling BSE Equity...")
    n_sub = bse_equity_backfill_subcategories()
    print(f"      Subcategories repaired : {n_sub:,} row(s)")
    n_url = bse_equity_backfill_attachment_urls()
    print(f"      Attachment URLs fixed  : {n_url:,} row(s)")
    print()


def cmd_stats(args):
    sources = _resolve_sources(args)
    for src in sources:
        print(f"\n{'='*60}")
        print(f"  {src.upper().replace('_',' ')} STATS")
        print(f"  DB: {os.path.abspath(DB_PATHS[src])}")
        try:
            size = os.path.getsize(DB_PATHS[src])
            print(f"  Size: {size:,} bytes")
        except FileNotFoundError:
            print("  (DB not created yet — run `fetch` first)")
            continue

        if src == "bse_equity":
            s = bse_equity_stats()
            print(f"  Announcements : {s['total']:,} rows")
            if s["categories"]:
                print("\n  By category:")
                for r in s["categories"]:
                    print(f"    {(r['category'] or 'Unknown'):35s} {r['n']:>6,}")
            if s["recent_runs"]:
                print("\n  Recent runs:")
                for r in s["recent_runs"]:
                    print(f"    [{r['status']:7s}] {r['started_at']}  fetched={r['records_fetched']} inserted={r['records_inserted']}")
        elif src == "bse_sme":
            s = bse_sme_stats()
            print(f"  Announcements : {s['ann_total']:,} rows")
            print(f"  Corp actions  : {s['corp_total']:,} rows")
            if s["ann_cats"]:
                print("\n  Announcements by category:")
                for r in s["ann_cats"]:
                    print(f"    {(r['category'] or 'Unknown'):35s} {r['n']:>6,}")
            if s["last_fetch"]:
                print("\n  Recent fetches:")
                for r in s["last_fetch"]:
                    print(f"    [{r['status']:5s}] {r['source']:15s} {r['finished_at']}  +{r['rows_new']} new")
        else:
            s = nse_stats(src)
            print(f"  Announcements : {s['total']:,} rows")
            if s["top_symbols"]:
                print("\n  Top symbols:")
                for r in s["top_symbols"]:
                    print(f"    {(r['symbol'] or 'N/A'):20s} {r['n']:>6,}")
    print()


def cmd_export(args):
    from_date, to_date = _resolve_dates(args)
    sources = _resolve_sources(args)
    out_path = Path(args.file)

    for src in sources:
        suffix   = f"_{src}" if len(sources) > 1 else ""
        out_file = out_path.with_stem(out_path.stem + suffix)
        rows_to_write = []

        if src == "bse_equity":
            rows_to_write = bse_equity_query(from_date, to_date)
        elif src == "bse_sme":
            fd = _ddmmyyyy_to_iso_prefix(from_date)
            td = _ddmmyyyy_to_iso_prefix(to_date)
            ann  = bse_sme_query_ann(from_dt=fd, to_dt=td)
            corp = bse_sme_query_corp(from_dt=fd, to_dt=td)
            with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
                if ann:
                    f.write("# ANNOUNCEMENTS\n")
                    w = csv.DictWriter(f, fieldnames=ann[0].keys())
                    w.writeheader(); w.writerows(ann); f.write("\n")
                if corp:
                    f.write("# CORP ACTIONS\n")
                    w = csv.DictWriter(f, fieldnames=corp[0].keys())
                    w.writeheader(); w.writerows(corp)
            print(f"  [{src.upper()}] Exported {len(ann)+len(corp)} rows → {out_file.resolve()}")
            continue
        else:
            rows_to_write = nse_query(src, from_date, to_date)

        if rows_to_write:
            with open(out_file, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=rows_to_write[0].keys())
                w.writeheader()
                w.writerows(rows_to_write)
        print(f"  [{src.upper()}] Exported {len(rows_to_write)} rows → {out_file.resolve()}")


def cmd_schedule(args):
    try:
        import schedule as sched
    except ImportError:
        log.error("'schedule' library not installed. Run: pip install schedule")
        sys.exit(1)

    sources = _resolve_sources(args)
    every   = getattr(args, "every", "hourly")

    log.info("=== SCHEDULER started — every=%s  sources=%s ===", every, sources)
    print(f"\n⏰  Scheduler running — {every} fetch for: {', '.join(s.upper() for s in sources)}")
    print("   Press Ctrl+C to stop.\n")

    def job():
        today = datetime.today()
        fd = (today - timedelta(days=1)).strftime("%d-%m-%Y")
        td = today.strftime("%d-%m-%Y")
        log.info("--- Scheduled fetch: %s → %s ---", fd, td)
        for src in sources:
            try:
                if src == "bse_equity":
                    f, i = fetch_bse_equity(fd, td)
                elif src == "bse_sme":
                    f = fetch_bse_sme(fd, td); i = f
                else:
                    f, i = fetch_nse(src, fd, td)
                log.info("[%s] %d fetched, %d new", src.upper(), f, i)
                rpt = generate_report(src, fd, td)
                log.info("[%s] Report → %s", src.upper(), rpt)
                print(f"  ✅  [{src.upper()}] {i} new  |  report → {rpt.name}  [{datetime.now().strftime('%H:%M:%S')}]")
            except Exception as e:
                log.error("[%s] Scheduled job failed: %s", src.upper(), e)

    job()   # run once immediately on start

    if every == "hourly":
        sched.every(1).hours.do(job)
    elif every == "daily":
        sched.every().day.at("08:00").do(job)
    elif every == "weekly":
        sched.every().monday.at("08:00").do(job)
    elif every == "3hours":
        sched.every(3).hours.do(job)

    try:
        while True:
            sched.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n  Scheduler stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT  — CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="market_announcements",
        description=(
            "Unified Indian Market Announcements Scraper\n"
            "4 independent SQLite databases:\n"
            "  bse_equity → bse_equity.db\n"
            "  bse_sme    → bse_sme.db\n"
            "  nse_equity → nse_equity.db\n"
            "  nse_sme    → nse_sme.db"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    def add_source(p, default="all"):
        p.add_argument(
            "--source", default=default,
            choices=["all"] + ALL_SOURCES,
            help="Which scraper/DB to use (default: all)",
        )

    def add_dates(p):
        p.add_argument("--from", dest="from_date", default=None,
                       metavar="DD-MM-YYYY",
                       help="Start date DD-MM-YYYY (default: today)")
        p.add_argument("--to",   dest="to_date",   default=None,
                       metavar="DD-MM-YYYY",
                       help="End date DD-MM-YYYY   (default: today)")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── fetch ──────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "fetch",
        help="Fetch announcements and store in DBs. "
             "Default window: today only. Use --yesterday for the previous day.",
    )
    add_source(p)
    add_dates(p)
    p.add_argument(
        "--yesterday", action="store_true", default=False,
        help="Fetch yesterday only (overrides --from/--to). "
             "Useful for explicit previous-day re-runs.",
    )
    p.add_argument(
        "--symbols", nargs="*", default=None,
        metavar="SYM",
        help="BSE Equity only: specific symbols (e.g. --symbols TCS INFY). "
             "Omit for market-wide fetch.",
    )
    p.add_argument(
        "--ann",  action="store_true",
        help="BSE SME only: fetch announcements only"
    )
    p.add_argument(
        "--corp", action="store_true",
        help="BSE SME only: fetch corp actions only"
    )
    p.set_defaults(func=cmd_fetch)

    # ── query ──────────────────────────────────────────────────────────────
    p = sub.add_parser("query", help="Query stored announcements")
    add_source(p)
    add_dates(p)
    p.add_argument("--scrip", default=None, metavar="NAME",
                   help="Filter by scrip/symbol (partial match)")
    p.add_argument("--output", default=None, metavar="FILE.json",
                   help="Export results to a JSON file")
    p.set_defaults(func=cmd_query)

    # ── backfill ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "backfill",
        help="One-time repair of existing BSE Equity rows: re-derive "
             "subcategory_id from stored raw_json, and fix attachment_url "
             "(fixes the SUBCATNAME field-name bug, 2026-07-10).",
    )
    p.set_defaults(func=cmd_backfill)

    # ── report ─────────────────────────────────────────────────────────────
    p = sub.add_parser("report", help="Generate formatted text reports")
    add_source(p)
    add_dates(p)
    p.set_defaults(func=cmd_report)

    # ── stats ──────────────────────────────────────────────────────────────
    p = sub.add_parser("stats", help="Show database statistics")
    add_source(p)
    p.set_defaults(func=cmd_stats)

    # ── export ─────────────────────────────────────────────────────────────
    p = sub.add_parser("export", help="Export stored data to CSV")
    add_source(p)
    add_dates(p)
    p.add_argument("file", help="Output CSV path (e.g. out.csv)")
    p.set_defaults(func=cmd_export)

    # ── schedule ───────────────────────────────────────────────────────────
    p = sub.add_parser("schedule", help="Auto-fetch + report on a recurring schedule")
    add_source(p)
    p.add_argument(
        "--every", default="hourly",
        choices=["hourly", "3hours", "daily", "weekly"],
        help="Repeat interval (default: hourly)",
    )
    p.set_defaults(func=cmd_schedule)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
