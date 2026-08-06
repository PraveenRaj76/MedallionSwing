"""
Local swing-universe live market sync — Midcap 150 + Smallcap 50.

Usage (from project folder, with venv active):

  # 1) Refresh Mid150+Small50 from NSE + load LIVE prices/technicals
  python sync_nifty500_local.py

  # 2) Same + Screener.in fundamentals (slower)
  python sync_nifty500_local.py --with-fundamentals

  # 3) Then run the app against the live SQLite DB
  streamlit run app.py

Env:
  MEDALLION_MARKET_MODE=live          (forced by this script)
  MEDALLION_SSL_VERIFY=0              (use on corporate SSL intercept)
  MEDALLION_DB_PATH=...               (optional custom DB path)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Force live before importing engines
os.environ["MEDALLION_MARKET_MODE"] = "live"
if "MEDALLION_SSL_VERIFY" not in os.environ:
    # Local Windows / corp networks often need verify off for Yahoo
    os.environ["MEDALLION_SSL_VERIFY"] = "0"

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_swing_universe")

UNIVERSE_PATH = BASE / "data" / "nse_universe.txt"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync live Midcap 150 + Smallcap 50 into local SQLite"
    )
    parser.add_argument(
        "--with-fundamentals",
        action="store_true",
        help="Also scrape Screener.in fundamentals (slow)",
    )
    parser.add_argument(
        "--skip-universe-refresh",
        action="store_true",
        help="Use existing data/nse_universe.txt without re-downloading NSE CSVs",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="How many tickers per progress batch (default 25)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Wipe screener_leaderboard before sync (removes any old mock rows)",
    )
    args = parser.parse_args()

    import database_engine as db
    import nse_data_provider as nse
    import importlib

    # Reload provider so MARKET_MODE / SSL pick up env forced above
    importlib.reload(nse)
    importlib.reload(db)

    if not nse.is_live_mode():
        logger.error("Live mode not active — aborting")
        return 1

    db.init_database()
    if args.clear:
        cleared = db.clear_leaderboard()
        logger.info("Cleared leaderboard rows: %s", cleared)

    if args.skip_universe_refresh and UNIVERSE_PATH.exists():
        symbols = nse.load_universe()
        logger.info("Using existing universe: %s tickers", len(symbols))
    else:
        try:
            symbols = nse.refresh_swing_universe(write_file=True)
        except Exception as exc:
            logger.warning("NSE list refresh failed (%s) — using local universe file", exc)
            symbols = nse.load_universe()
            if not symbols:
                logger.error("No universe available")
                return 1

    # Reload universe after file write
    importlib.reload(nse)
    symbols = nse.load_universe()
    total = len(symbols)
    logger.info(
        "Starting LIVE sync for %s tickers (%s) | fundamentals=%s",
        total,
        nse.universe_label(),
        args.with_fundamentals,
    )

    started = time.time()
    fail_count = 0
    batch = max(1, int(args.batch_size))

    for i in range(0, total, batch):
        chunk = symbols[i : i + batch]
        rows = nse.refresh_universe_live(
            tickers=chunk,
            max_workers=6,
            include_fundamentals=args.with_fundamentals,
            deadline_sec=None,
        )
        if rows:
            db.upsert_leaderboard_rows(rows)
        miss = len(chunk) - len(rows)
        fail_count += max(miss, 0)
        elapsed = time.time() - started
        done = min(i + batch, total)
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        logger.info(
            "Progress %s/%s | saved+%s | misses~%s | elapsed %.0fs | ETA %.0fs",
            done,
            total,
            len(rows),
            miss,
            elapsed,
            eta,
        )

    final = db.get_leaderboard(limit=1000)
    n_db = 0 if final is None else len(final)
    logger.info("=" * 60)
    logger.info("DONE — live rows in DB: %s / universe %s", n_db, total)
    logger.info("Failures / skips this run: ~%s", fail_count)
    logger.info("DB path: %s", db.DATABASE_PATH)
    logger.info("Next:  .\\venv\\Scripts\\streamlit.exe run app.py")
    logger.info("=" * 60)

    if n_db < 30:
        logger.error("Too few live rows — check network / Yahoo access")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
