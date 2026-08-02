"""
Medallion Swing — Forward-Test Signal Validation Pipeline
Live NSE quotes · Fixed Quantity = 1 · SUCCESSFUL TRADE / BAD TRADE clearance
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import database_engine as db
import nse_data_provider as nse

logger = logging.getLogger(__name__)

SYNC_COOLDOWN_MINUTES = 15
FUNDAMENTAL_REFRESH_HOURS = 24
RSI_OVERBOUGHT = 65.0
FIXED_QUANTITY = 1


def should_skip_heavy_sync() -> Tuple[bool, Optional[datetime]]:
    last_updated = db.get_leaderboard_last_updated()
    if last_updated is None:
        return False, None
    if datetime.utcnow() - last_updated < timedelta(minutes=SYNC_COOLDOWN_MINUTES):
        return True, last_updated
    return False, last_updated


def _fundamentals_stale() -> bool:
    last = db.get_leaderboard_last_updated()
    if last is None:
        return True
    # Re-scrape fundamentals at most once per day; prices still refresh every SYNC_COOLDOWN
    # We use a lightweight flag via max(last_updated) age > FUNDAMENTAL_REFRESH_HOURS
    # when caller requests include_fundamentals.
    return datetime.utcnow() - last >= timedelta(hours=FUNDAMENTAL_REFRESH_HOURS)


def refresh_screener_quotes(force: bool = False, include_fundamentals: Optional[bool] = None) -> int:
    """
    Refresh screener from live NSE (+ Screener.in fundamentals).
    Falls back to mock seed only if live mode is disabled or every fetch fails.
    """
    try:
        if not nse.is_live_mode():
            db.ensure_mock_leaderboard()
            return _refresh_mock_jitter()

        if include_fundamentals is None:
            include_fundamentals = force or _fundamentals_stale() or db.leaderboard_is_empty()

        rows = nse.refresh_universe_live(
            tickers=nse.load_universe(),
            max_workers=4,
            include_fundamentals=include_fundamentals,
        )
        if not rows:
            logger.error("Live universe refresh returned 0 rows")
            if db.leaderboard_is_empty():
                db.ensure_mock_leaderboard()
            return 0

        # When skipping fundamentals scrape, merge prior fund fields from DB
        if not include_fundamentals:
            merged = []
            for row in rows:
                prior = db.get_ticker_row(row["ticker"])
                if prior is not None:
                    for key in (
                        "company_name",
                        "description",
                        "sector",
                        "industry",
                        "roic",
                        "net_debt_ebitda",
                        "peg_ratio",
                        "interest_coverage",
                        "promoter_pledge_pct",
                        "yoy_profit_growth",
                        "fundamental_score",
                    ):
                        if prior.get(key) is not None:
                            row[key] = prior.get(key)
                    row["technical_score"] = nse._score_technical(row)
                    row["composite_score"] = round(
                        float(row.get("fundamental_score") or 0) + float(row["technical_score"]),
                        1,
                    )
                merged.append(row)
            rows = merged

        db.upsert_leaderboard_rows(rows)
        return len(rows)
    except Exception as exc:
        logger.error("refresh_screener_quotes failed: %s", exc)
        if db.leaderboard_is_empty():
            db.ensure_mock_leaderboard()
        return 0


def _refresh_mock_jitter() -> int:
    """Offline test path — small jitter on seeded mock rows."""
    import random

    frame = db.get_leaderboard(limit=500)
    if frame is None or frame.empty:
        db.ensure_mock_leaderboard()
        frame = db.get_leaderboard(limit=500)
    if frame is None or frame.empty:
        return 0
    refreshed: List[Dict[str, Any]] = []
    for _, row in frame.iterrows():
        payload = row.to_dict()
        base = float(payload.get("close_price", 0) or 0)
        atr = float(payload.get("atr_value", 1) or 1)
        payload["close_price"] = round(max(1.0, base * (1 + random.uniform(-0.01, 0.01))), 2)
        payload["atr_value"] = round(max(0.05, atr * random.uniform(0.95, 1.05)), 2)
        rsi = float(np.clip(float(payload.get("rsi_14", 50) or 50) + random.uniform(-1.5, 1.5), 20, 80))
        payload["rsi_14"] = round(rsi, 2)
        payload["is_buyable"] = _recompute_buyable(
            payload["close_price"], float(payload.get("sma_200", 0) or 0), rsi
        )
        refreshed.append(payload)
    db.upsert_leaderboard_rows(refreshed)
    return len(refreshed)


def _recompute_buyable(close_price: float, sma_200: float, rsi_14: float) -> int:
    if close_price <= sma_200 or rsi_14 > RSI_OVERBOUGHT:
        return 0
    return 1


def ensure_ticker_live(ticker: str, include_fundamentals: bool = True) -> Optional[pd.Series]:
    """Resolve any NSE ticker on demand (Search Profile) and cache into leaderboard."""
    ticker = nse.normalize_ticker(ticker)
    if not nse.is_live_mode():
        return db.get_ticker_row(ticker)

    prior = db.get_ticker_row(ticker)
    prior_dict = prior.to_dict() if prior is not None else None
    try:
        bench = nse.fetch_ohlcv(nse.BENCHMARK, range_param="6mo", interval="1d")
        row = nse.build_live_row(
            ticker,
            bench_frame=bench,
            include_fundamentals=include_fundamentals,
            prior=prior_dict,
        )
        if row:
            db.upsert_leaderboard_rows([row])
            return pd.Series(row)
    except Exception as exc:
        logger.error("ensure_ticker_live failed for %s: %s", ticker, exc)
    return prior


def validate_active_signals(user_id: int) -> List[Dict[str, Any]]:
    """
    Clearance loop: mark 1-share signals to market and auto-close on target/stop.
    Target hit  -> SUCCESSFUL TRADE
    Stop hit    -> BAD TRADE
    """
    clearances: List[Dict[str, Any]] = []
    try:
        positions = db.get_active_positions(user_id)
        if positions is None or positions.empty:
            return clearances

        for _, pos in positions.iterrows():
            if int(pos["user_id"]) != int(user_id):
                continue
            position_id = int(pos["position_id"])
            ticker = str(pos["ticker"]).upper()
            entry_price = float(pos["entry_price"])
            stop_loss = float(pos["stop_loss"])
            target = float(pos["target"])
            quantity = int(pos.get("quantity") or FIXED_QUANTITY)

            market = db.get_ticker_row(ticker)
            if market is None and nse.is_live_mode():
                market = ensure_ticker_live(ticker, include_fundamentals=False)

            if market is not None:
                current_price = float(market.get("close_price", entry_price))
            else:
                current_price = float(pos.get("current_price") or entry_price)

            unrealized = (current_price - entry_price) * quantity
            db.update_position_mark(position_id, current_price, unrealized)

            exit_status = None
            if current_price >= target:
                exit_status = db.EXIT_SUCCESS
            elif current_price <= stop_loss:
                exit_status = db.EXIT_BAD

            if exit_status:
                ok, message, pnl = db.close_signal(
                    user_id=user_id,
                    position_id=position_id,
                    exit_price=current_price,
                    exit_status=exit_status,
                )
                clearances.append(
                    {
                        "ticker": ticker,
                        "position_id": position_id,
                        "exit_status": exit_status,
                        "exit_price": current_price,
                        "final_pnl": pnl,
                        "success": ok,
                        "message": message,
                    }
                )
        return clearances
    except Exception as exc:
        logger.error("validate_active_signals failed for user %s: %s", user_id, exc)
        return clearances


def _looks_like_legacy_mock_board() -> bool:
    """Detect old mock-only leaderboard so live mode upgrades force a real NSE pull."""
    try:
        frame = db.get_leaderboard(limit=200)
        if frame is None or frame.empty:
            return True
        mock_set = {str(m["ticker"]).upper() for m in db.MOCK_LEADERBOARD}
        tickers = {str(t).upper() for t in frame["ticker"].tolist()}
        return tickers.issubset(mock_set) and len(tickers) <= len(mock_set)
    except Exception:
        return False


def sync_user_and_screener_data(user_id: int, force: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "skipped_heavy_sync": False,
        "last_updated": None,
        "rows_refreshed": 0,
        "clearances": [],
        "message": "",
        "live_mode": nse.is_live_mode(),
    }
    try:
        db.init_database()
        if nse.is_live_mode() and _looks_like_legacy_mock_board():
            force = True
        skip, last_updated = should_skip_heavy_sync()
        result["last_updated"] = last_updated.isoformat(sep=" ") if last_updated else None

        if skip and not force and not db.leaderboard_is_empty():
            result["skipped_heavy_sync"] = True
            result["clearances"] = validate_active_signals(user_id)
            mode = "LIVE NSE" if nse.is_live_mode() else "MOCK"
            result["message"] = (
                f"{mode} cache hit — quotes fresh within {SYNC_COOLDOWN_MINUTES} minutes."
            )
            return result

        result["rows_refreshed"] = refresh_screener_quotes(force=force)
        result["clearances"] = validate_active_signals(user_id)
        fresh_ts = db.get_leaderboard_last_updated()
        result["last_updated"] = fresh_ts.isoformat(sep=" ") if fresh_ts else None
        mode = "LIVE NSE" if nse.is_live_mode() else "MOCK"
        result["message"] = (
            f"{mode} sync — refreshed {result['rows_refreshed']} rows, "
            f"{len(result['clearances'])} clearance(s)."
        )
        return result
    except Exception as exc:
        logger.error("sync_user_and_screener_data failed: %s", exc)
        if db.leaderboard_is_empty():
            db.ensure_mock_leaderboard()
        result["message"] = f"Sync degraded: {exc}"
        result["clearances"] = validate_active_signals(user_id)
        return result


def check_buyability(row: pd.Series) -> Tuple[bool, str]:
    close_price = float(row.get("close_price", row.get("cmp", 0.0)))
    sma_200 = float(row.get("sma_200", 0.0))
    rsi_14 = float(row.get("rsi_14", 50.0))
    if close_price <= sma_200:
        return (
            False,
            f"SIGNAL BLOCKED: Price below 200-day SMA "
            f"(₹{close_price:.2f} ≤ ₹{sma_200:.2f}).",
        )
    if rsi_14 > RSI_OVERBOUGHT:
        return (
            False,
            f"⚠️ OVEREXTENDED: 14-Day RSI at {rsi_14:.1f}% "
            f"(threshold {RSI_OVERBOUGHT:.0f}). Signal entry locked.",
        )
    return True, "SIGNAL CLEAR: Passes 200 SMA / RSI filters."


def build_trade_levels(close_price: float, atr: float) -> Dict[str, float]:
    stop_loss = close_price - (2.5 * atr)
    target = close_price + (6.0 * atr)
    risk = close_price - stop_loss
    reward = target - close_price
    rrr = (reward / risk) if risk > 0 else 0.0
    return {
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "rrr": round(rrr, 2),
        "quantity": FIXED_QUANTITY,
    }


def generate_price_history(ticker: str, close_price: float, periods: int = 250) -> pd.DataFrame:
    """Prefer live NSE OHLC; fall back to synthetic series only in mock mode / total failure."""
    if nse.is_live_mode():
        live = nse.fetch_chart_history(ticker, periods=periods)
        if live is not None and not live.empty:
            return live

    dates = pd.date_range(end=datetime.now(), periods=periods, freq="D")
    seed = abs(hash(str(ticker).upper())) % (2**32)
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, periods)
    prices = close_price * np.exp(np.cumsum(returns))
    if prices[-1] != 0:
        prices = prices * (close_price / prices[-1])
    opens = prices * (1.0 + rng.uniform(-0.01, 0.01, periods))
    highs = np.maximum(prices, opens) * (1.0 + np.abs(rng.normal(0, 0.008, periods)))
    lows = np.minimum(prices, opens) * (1.0 - np.abs(rng.normal(0, 0.008, periods)))
    volumes = rng.lognormal(mean=16.0, sigma=0.5, size=periods).astype(int)
    return pd.DataFrame(
        {"date": dates, "open": opens, "high": highs, "low": lows, "close": prices, "volume": volumes}
    )


def compute_sma(prices: np.ndarray, period: int) -> np.ndarray:
    if len(prices) < period:
        return np.array([])
    return np.convolve(prices, np.ones(period) / period, mode="valid")


def compute_rsi_series(prices: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray]:
    if len(prices) <= period:
        return np.array([]), np.array([])
    deltas = np.diff(prices)
    rsi_values: List[float] = []
    for i in range(period, len(prices)):
        window = deltas[i - period : i]
        gains = window[window >= 0].sum() / period
        losses = -window[window < 0].sum() / period
        rsi = 100.0 if losses == 0 else 100.0 - (100.0 / (1.0 + gains / losses))
        rsi_values.append(float(rsi))
    return np.arange(period, len(prices)), np.asarray(rsi_values, dtype=float)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            pass
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def enrich_closed_trade_row(row: pd.Series) -> Dict[str, Any]:
    entry = float(row.get("entry_price", 0.0) or 0.0)
    exit_px = float(row.get("exit_price", 0.0) or 0.0)
    qty = int(row.get("quantity") or FIXED_QUANTITY)
    pnl = float(row.get("final_pnl") if row.get("final_pnl") is not None else (exit_px - entry) * qty)
    pct = ((exit_px - entry) / entry * 100.0) if entry > 0 else 0.0
    entry_dt = _parse_dt(row.get("entry_timestamp") or row.get("entry_date"))
    exit_dt = _parse_dt(row.get("exit_timestamp") or row.get("exit_date"))
    days = 0
    if entry_dt and exit_dt:
        days = max(int((exit_dt - entry_dt).total_seconds() // 86400), 0)
    status = str(row.get("exit_status") or row.get("exit_reason") or "")
    return {
        "ticker": str(row.get("ticker", "")),
        "exit_status": status,
        "absolute_delta": round(pnl, 2),
        "pct_return": round(pct, 2),
        "days_elapsed": days,
        "velocity_label": f"Achieved in {days} Day{'s' if days != 1 else ''}",
        "entry_price": entry,
        "exit_price": exit_px,
        "entry_timestamp": row.get("entry_timestamp"),
        "exit_timestamp": row.get("exit_timestamp"),
    }


def compute_forward_test_scorecard(user_id: int) -> Dict[str, Any]:
    closed = db.get_closed_trades(user_id)
    total = 0 if closed is None or closed.empty else len(closed)
    successful = 0
    total_rupee = 0.0
    trade_rows: List[Dict[str, Any]] = []

    if total > 0:
        for _, row in closed.iterrows():
            if int(row.get("user_id", user_id)) != int(user_id):
                continue
            enriched = enrich_closed_trade_row(row)
            trade_rows.append(enriched)
            status = enriched["exit_status"].upper()
            if status == db.EXIT_SUCCESS.upper() or status == "TARGET_HIT":
                successful += 1
            total_rupee += float(enriched["absolute_delta"])

    win_rate = (successful / total * 100.0) if total > 0 else 0.0
    return {
        "total_signals_tracked": total,
        "successful_trades": successful,
        "win_rate_pct": round(win_rate, 2),
        "total_realized_rupee_return": round(total_rupee, 2),
        "trades": trade_rows,
    }
