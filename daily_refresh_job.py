"""
Overnight full daily refresh (prices + fundamentals + technicals).

Designed for Windows Task Scheduler at 01:00 IST so the Screener is ready
when you open the app in the morning.

Usage:
  .\\venv\\Scripts\\python.exe daily_refresh_job.py
  .\\venv\\Scripts\\python.exe daily_refresh_job.py --batch 12 --sleep 0.4
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime


def main() -> int:
    os.environ.setdefault("MEDALLION_MARKET_MODE", "live")
    os.environ.setdefault("MEDALLION_SSL_VERIFY", "0")

    parser = argparse.ArgumentParser(description="Medallion Swing overnight daily refresh")
    parser.add_argument("--batch", type=int, default=12, help="Internal batch size (5–20)")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause between steps (sec)")
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Do not clear the board — resume today's incomplete load",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=0,
        help="Stop after N steps (0 = run until complete or stuck)",
    )
    args = parser.parse_args()

    import data_pipeline as pipeline
    import database_engine as db

    db.init_database()
    t0 = time.time()
    today = db.today_ist()
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_refresh_log.txt")

    def log(msg: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
        print(line, flush=True)
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    log(f"=== Daily refresh job start (IST date {today}) ===")

    if not args.skip_reset:
        started = pipeline.begin_daily_refresh()
        log(started.get("message", "begin_daily_refresh done"))
        if started.get("status") == "complete":
            log("Already complete (mock or instant).")
            return 0
    else:
        status = pipeline.daily_load_status()
        if not status.get("is_today"):
            started = pipeline.begin_daily_refresh()
            log(started.get("message", "begin (was stale)"))
        else:
            db.set_screener_refresh_state(status="running", message="Resuming overnight job…")
            log(f"Resuming — complete {status.get('complete')}/{status.get('target')}")

    steps = 0
    stagnant = 0
    last_complete = -1
    while True:
        steps += 1
        if args.max_steps and steps > args.max_steps:
            log(f"Stopped after --max-steps={args.max_steps}")
            return 2

        step = pipeline.run_daily_refresh_step(batch_size=max(5, min(int(args.batch), 20)))
        status = step.get("status") or pipeline.daily_load_status()
        complete = int(status.get("complete") or 0)
        target = int(status.get("target") or 200)
        log(
            f"[{steps}] phase={step.get('phase')} "
            f"complete={complete}/{target} "
            f"prices={status.get('prices')} fund={status.get('fundamentals')} "
            f"tech={status.get('technicals')} | {step.get('message', '')[:120]}"
        )

        if step.get("done") or complete >= target:
            elapsed = round(time.time() - t0, 1)
            if complete >= target:
                db.set_screener_refresh_state(
                    status="complete",
                    message=f"Overnight job complete — {complete}/{target} at {db.today_ist()}.",
                )
                log(f"SUCCESS in {elapsed}s — {complete}/{target}")
                return 0
            log(f"Finished with incomplete data {complete}/{target} after {elapsed}s")
            db.set_screener_refresh_state(
                status="failed",
                message=f"Overnight job incomplete — {complete}/{target}. Open app and Refresh to retry.",
            )
            return 1

        if complete == last_complete:
            stagnant += 1
        else:
            stagnant = 0
            last_complete = complete
        # After many no-progress fund steps, still keep trying price retry path;
        # hard-stop only if stuck for a very long time (avoids infinite empty spin).
        if stagnant >= 80:
            log(f"No progress for {stagnant} steps — aborting as failed.")
            db.set_screener_refresh_state(
                status="failed",
                message=f"Overnight job stalled at {complete}/{target}.",
            )
            return 1

        if args.sleep > 0:
            time.sleep(args.sleep)


if __name__ == "__main__":
    sys.exit(main())
