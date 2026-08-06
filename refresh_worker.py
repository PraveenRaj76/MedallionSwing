"""
Background Nifty daily refresh worker.

Runs fill batches in a daemon thread so Screener load continues until complete
even when the user navigates to Search / Forward-Test.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_user_id: Optional[int] = None
_batch_size = 12


def is_worker_alive() -> bool:
    t = _thread
    return t is not None and t.is_alive()


def stop_worker(*, pause: bool = True) -> None:
    """Signal worker to stop. Optionally mark DB status paused."""
    global _thread
    _stop.set()
    t = _thread
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=2.0)
    with _lock:
        if _thread is t:
            _thread = None
    if pause:
        try:
            import database_engine as db
            import data_pipeline as pipeline

            cur = db.get_meta(db.META_SCREENER_STATUS, "idle") or "idle"
            if cur == "running":
                status = pipeline.daily_load_status()
                db.set_screener_refresh_state(
                    status="paused",
                    message=(
                        f"Paused at {status.get('complete', 0)}/{status.get('target', 200)} ready — "
                        "click Resume load to continue in background."
                    ),
                )
        except Exception as exc:
            logger.warning("stop_worker pause meta failed: %s", exc)


def start_worker(user_id: Optional[int] = None, batch_size: int = 12) -> Dict[str, Any]:
    """Start (or keep) background load until swing universe ready / exhausted."""
    global _thread, _user_id, _batch_size

    with _lock:
        if is_worker_alive():
            return {"started": False, "already_running": True, "message": "Background load already running."}

        _stop.clear()
        _user_id = user_id
        _batch_size = max(8, min(int(batch_size), 16))

        def _run() -> None:
            import data_pipeline as pipeline
            import database_engine as db

            db.set_screener_refresh_state(
                status="running",
                message="Background Nifty load running (continues if you leave Screener)…",
            )
            # One-off BSE scrip-code map, otherwise the first parallel batch serialises on it
            try:
                import free_extra_sources as extra

                extra.warm_up()
            except Exception as exc:
                logger.warning("free-extra warm-up skipped: %s", exc)
            # Re-base ETA on current progress so a slow first stock can't poison the estimate
            try:
                status0 = pipeline.daily_load_status()
                db.start_refresh_eta_clock(complete0=int(status0.get("complete") or 0))
            except Exception:
                pass
            logger.info("daily refresh worker started user_id=%s", _user_id)
            idle_rounds = 0
            try:
                while not _stop.is_set():
                    status = pipeline.daily_load_status()
                    if not status.get("is_today"):
                        break
                    complete = int(status.get("complete") or 0)
                    target = int(status.get("target") or 200)
                    pending = int(status.get("pending") or 0)
                    if complete >= target or pending <= 0:
                        failed_n = int(status.get("failed") or 0)
                        db.set_screener_refresh_state(
                            status="complete" if failed_n == 0 else "failed",
                            message=(
                                f"Swing universe ready — {complete}/{target}"
                                + (f" ({failed_n} failed after retries)." if failed_n else ".")
                            ),
                        )
                        break

                    step = pipeline.run_daily_refresh_step(
                        user_id=_user_id,
                        batch_size=_batch_size,
                    )
                    if step.get("done"):
                        break
                    # Safety: if a step makes no pending progress repeatedly, stop
                    status2 = step.get("status") or pipeline.daily_load_status()
                    if int(status2.get("pending") or 0) <= 0:
                        idle_rounds += 1
                    else:
                        idle_rounds = 0
                    if idle_rounds >= 3:
                        break
                    time.sleep(0.12)
            except Exception as exc:
                logger.exception("daily refresh worker crashed: %s", exc)
                try:
                    import database_engine as db

                    db.set_screener_refresh_state(
                        status="failed",
                        message=f"Background load stopped with error: {exc}",
                    )
                except Exception:
                    pass
            finally:
                logger.info("daily refresh worker exited")
                with _lock:
                    global _thread
                    _thread = None

        _thread = threading.Thread(target=_run, name="medallion-daily-refresh", daemon=True)
        _thread.start()
        return {"started": True, "already_running": False, "message": "Background Nifty load started."}


def worker_snapshot() -> Dict[str, Any]:
    return {"alive": is_worker_alive(), "stopping": _stop.is_set()}
