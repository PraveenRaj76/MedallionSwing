"""
Fill multi-source fundamentals for every unverified ticker in the DB.

Usage (from project root, venv Python):
  $env:MEDALLION_MARKET_MODE="live"
  $env:MEDALLION_SSL_VERIFY="0"
  .\\venv\\Scripts\\python.exe fill_all_fundamentals.py
  .\\venv\\Scripts\\python.exe fill_all_fundamentals.py --batch 20 --sleep 0.5
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> int:
    os.environ.setdefault("MEDALLION_MARKET_MODE", "live")
    os.environ.setdefault("MEDALLION_SSL_VERIFY", "0")

    parser = argparse.ArgumentParser(description="Fill fundamentals for full leaderboard")
    parser.add_argument("--batch", type=int, default=15, help="Tickers per batch (5–40)")
    parser.add_argument("--sleep", type=float, default=0.3, help="Pause between batches (sec)")
    parser.add_argument("--max-batches", type=int, default=0, help="Stop after N batches (0=all)")
    parser.add_argument("--no-ohlcv", action="store_true", help="Skip Yahoo OHLCV (faster fund-only)")
    args = parser.parse_args()

    import data_pipeline as pipeline
    import database_engine as db

    batch = max(5, min(int(args.batch), 40))
    with_ohlcv = not args.no_ohlcv
    cov0 = db.fundamentals_coverage()
    print(
        f"Start: verified {cov0['verified']}/{cov0['total']} "
        f"(missing {cov0['missing']}), batch={batch}, ohlcv={with_ohlcv}",
        flush=True,
    )
    if cov0["missing"] <= 0:
        print("Already complete.", flush=True)
        return 0

    t0 = time.time()
    n_batches = 0
    while True:
        cov = db.fundamentals_coverage()
        if cov["missing"] <= 0:
            break
        n_batches += 1
        if args.max_batches and n_batches > args.max_batches:
            print(f"Stopped after --max-batches={args.max_batches}", flush=True)
            break
        result = pipeline.fill_fundamentals_batch(batch_size=batch, with_ohlcv=with_ohlcv)
        cov = result.get("coverage") or db.fundamentals_coverage()
        elapsed = time.time() - t0
        done = cov["verified"] - cov0["verified"]
        rate = done / max(elapsed, 1.0)
        eta_min = (cov["missing"] / max(rate, 1e-6)) / 60.0 if rate > 0 else float("nan")
        failed = result.get("failed") or []
        print(
            f"[{n_batches}] {result.get('message')} "
            f"failed={failed[:5]}{'…' if len(failed) > 5 else ''} "
            f"rate={rate:.2f}/s ETA≈{eta_min:.0f}m",
            flush=True,
        )
        if result.get("filled", 0) == 0 and not failed:
            # Nothing pending but coverage still missing — avoid spin
            print("No progress; exiting.", flush=True)
            break
        if args.sleep > 0:
            time.sleep(args.sleep)

    cov = db.fundamentals_coverage()
    print(
        f"Done in {round(time.time() - t0, 1)}s — "
        f"verified {cov['verified']}/{cov['total']} "
        f"(ohlcv tech {cov.get('ohlcv', 0)})",
        flush=True,
    )
    return 0 if cov["missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
