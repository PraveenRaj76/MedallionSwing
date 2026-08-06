"""
Free NSE cash quotes (no paid API key).

Working free sources:
  1) Groww LIVE_PRICE API  — field **ltp** (NOT "close", which is prev close)
  2) Moneycontrol pricefeed — pricecurrent
  3) Yahoo chart meta      — regularMarketPrice

Prefer live LTP. If LTP is unavailable, fall back to previous close and set
price_kind=PREV_CLOSE so the UI never mislabels yesterday's close as live.
There is NO free official NSE/BSE retail streaming API key.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MC_SC_ID_MAP = {
    "RELIANCE": "RI",
    "TCS": "TCS",
    "HDFCBANK": "HDF01",
    "INFY": "IT",
    "ICICIBANK": "ICI02",
    "ITC": "ITC",
    "SBIN": "SBI",
    "BHARTIARTL": "BTV",
    "LT": "LT",
    "AXISBANK": "UTI10",
    "HCLTECH": "HCL02",
    "WIPRO": "W",
}


def _ssl_verify() -> bool:
    return os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {
        "0",
        "false",
        "False",
        "no",
    }


def _get(url: str, referer: str = "", timeout: int = 15):
    from curl_cffi import requests as cr

    try:
        return cr.get(
            url,
            impersonate="chrome124",
            timeout=timeout,
            verify=_ssl_verify(),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Referer": referer or "https://groww.in/",
            },
        )
    except Exception as exc:
        logger.debug("live GET failed %s: %s", url[:70], exc)
        return None


def _num(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def fetch_groww_quote(ticker: str) -> Dict[str, Any]:
    """
    Groww NSE CASH live quote.
    CRITICAL: use **ltp**, never **close** (close = previous close).
    """
    symbol = ticker.strip().upper()
    out: Dict[str, Any] = {"source": "groww", "ok": False, "ticker": symbol}
    url = (
        "https://groww.in/v1/api/stocks_data/v1/tr_live_prices/"
        f"exchange/NSE/segment/CASH/{symbol}/latest"
    )
    resp = _get(url, "https://groww.in/stocks/")
    if resp is None or resp.status_code >= 400:
        return out
    try:
        data = resp.json() or {}
        # Prefer explicit LTP fields only — NEVER raw "close" (prev close on Groww)
        px = (
            _num(data.get("ltp"))
            or _num(data.get("lastPrice"))
            or _num(data.get("last_price"))
        )
        prev = _num(data.get("close"))  # Groww: previous close
        # If LTP missing but we have prev + dayChange, reconstruct
        if px is None and prev is not None and data.get("dayChange") is not None:
            ch = _num(data.get("dayChange"))
            if ch is not None:
                px = prev + ch
        if px is None or px <= 0:
            return out
        out["ok"] = True
        out["close_price"] = round(float(px), 2)  # app field name = CMP/LTP
        out["prev_close"] = round(float(prev), 2) if prev else None
        out["day_high"] = _num(data.get("high"))
        out["day_low"] = _num(data.get("low"))
        out["day_change"] = _num(data.get("dayChange"))
        out["day_change_pct"] = _num(data.get("dayChangePerc"))
        out["open"] = _num(data.get("open"))
        out["volume"] = _num(data.get("volume"))
        out["quote_type"] = data.get("type") or "LIVE_PRICE"
        out["fetched_at"] = _now_iso()
        ts = data.get("tsInMillis") or data.get("lastTradeTime")
        if ts:
            try:
                out["exchange_ts"] = datetime.fromtimestamp(int(ts) / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                pass
        return out
    except Exception as exc:
        logger.warning("groww parse failed %s: %s", symbol, exc)
        return out


def fetch_moneycontrol_quote(ticker: str) -> Dict[str, Any]:
    symbol = ticker.strip().upper()
    out: Dict[str, Any] = {"source": "moneycontrol", "ok": False, "ticker": symbol}
    sc_id = MC_SC_ID_MAP.get(symbol)
    if not sc_id:
        try:
            import multi_source_data as msd

            sc_id = msd._moneycontrol_sc_id(symbol)
        except Exception:
            sc_id = None
    if not sc_id:
        return out
    resp = _get(
        f"https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/{sc_id}",
        "https://www.moneycontrol.com/",
    )
    if resp is None or resp.status_code >= 400:
        return out
    try:
        payload = resp.json() or {}
        data = payload.get("data") or {}
        px = _num(data.get("pricecurrent"))
        if px is None or px <= 0:
            return out
        out["ok"] = True
        out["close_price"] = round(px, 2)
        out["prev_close"] = _num(data.get("priceprevclose"))
        out["day_high"] = _num(data.get("HIGH") or data.get("high"))
        out["day_low"] = _num(data.get("LOW") or data.get("low"))
        out["open"] = _num(data.get("OPEN") or data.get("open"))
        out["company_name"] = data.get("company") or data.get("SC_FULLNM")
        out["exchange_ts"] = data.get("lastupd")
        out["fetched_at"] = _now_iso()
        return out
    except Exception as exc:
        logger.warning("moneycontrol live parse failed %s: %s", symbol, exc)
        return out


def fetch_yahoo_ltp(ticker: str) -> Dict[str, Any]:
    """Yahoo regularMarketPrice (live/last) — not end-of-day close bar."""
    symbol = ticker.strip().upper()
    out: Dict[str, Any] = {"source": "yahoo", "ok": False, "ticker": symbol}
    ysym = f"{symbol}.NS"
    for host in (
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
    ):
        resp = _get(
            f"{host}/v8/finance/chart/{ysym}?range=1d&interval=1m",
            "https://finance.yahoo.com/",
        )
        if resp is None or resp.status_code >= 400:
            continue
        try:
            result = ((resp.json().get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            meta = result.get("meta") or {}
            px = _num(meta.get("regularMarketPrice"))
            if px is None or px <= 0:
                continue
            out["ok"] = True
            out["close_price"] = round(px, 2)
            out["prev_close"] = _num(meta.get("previousClose") or meta.get("chartPreviousClose"))
            out["day_high"] = _num(meta.get("regularMarketDayHigh"))
            out["day_low"] = _num(meta.get("regularMarketDayLow"))
            out["fetched_at"] = _now_iso()
            return out
        except Exception as exc:
            logger.debug("yahoo ltp failed %s: %s", symbol, exc)
    return out


def _classify_price_kind(quote: Dict[str, Any], *, is_fallback_prev: bool = False) -> str:
    """LIVE = traded LTP from feed; PREV_CLOSE = yesterday/last session close only."""
    if is_fallback_prev:
        return "PREV_CLOSE"
    px = _num(quote.get("close_price"))
    prev = _num(quote.get("prev_close"))
    if px is None:
        return "PREV_CLOSE"
    if prev is not None and abs(px - prev) < 0.005:
        # Unchanged vs prior close (closed market / flat) — still a real last print,
        # but label clearly so users know it matches yesterday's close.
        return "LAST"
    return "LIVE"


def fetch_live_quote(ticker: str) -> Dict[str, Any]:
    """
    Prefer live LTP with cross-check:
      Groww.ltp → Moneycontrol.pricecurrent → Yahoo.regularMarketPrice
    If no LTP, accept previous close and mark price_kind=PREV_CLOSE.
    """
    symbol = ticker.strip().upper()
    g = fetch_groww_quote(symbol)
    m = fetch_moneycontrol_quote(symbol)
    y = fetch_yahoo_ltp(symbol)

    candidates = [c for c in (g, m, y) if c.get("ok") and _num(c.get("close_price"))]
    if not candidates:
        # No LTP — try previous close from any partial response
        for raw in (g, m, y):
            prev = _num(raw.get("prev_close"))
            if prev and prev > 0:
                return {
                    "ok": True,
                    "ticker": symbol,
                    "source": raw.get("source") or "prev_close",
                    "close_price": round(prev, 2),
                    "prev_close": round(prev, 2),
                    "price_kind": "PREV_CLOSE",
                    "fetched_at": raw.get("fetched_at") or _now_iso(),
                    "sources_checked": [raw.get("source")],
                }
        return {"source": "none", "ok": False, "ticker": symbol, "price_kind": None}

    # Prefer Groww when LTP present; else Moneycontrol; else Yahoo
    primary = next((c for c in candidates if c.get("source") == "groww"), None)
    if primary is None:
        primary = next((c for c in candidates if c.get("source") == "moneycontrol"), None)
    if primary is None:
        primary = candidates[0]

    ltp = float(primary["close_price"])
    # Cross-check: if another source disagrees by >3%, prefer Moneycontrol/Yahoo over Groww
    others = [float(c["close_price"]) for c in candidates if c is not primary]
    if others:
        for alt in others:
            if abs(alt - ltp) / max(ltp, 1e-9) > 0.03:
                # large disagreement — prefer MC/Yahoo over Groww prev-close mistakes
                for prefer in ("moneycontrol", "yahoo"):
                    hit = next((c for c in candidates if c.get("source") == prefer), None)
                    if hit:
                        primary = hit
                        ltp = float(hit["close_price"])
                        break
                break

    # Final guard: never use a price that equals prev_close while day_change says otherwise
    prev = _num(primary.get("prev_close"))
    chg = _num(primary.get("day_change"))
    if prev is not None and chg is not None and abs(ltp - prev) < 0.02 and abs(chg) > 0.5:
        ltp = round(prev + chg, 2)
        primary = {**primary, "close_price": ltp, "corrected": "prev+dayChange"}

    out = dict(primary)
    out["ok"] = True
    out["ticker"] = symbol
    out["close_price"] = round(ltp, 2)
    if prev is not None:
        out["prev_close"] = round(prev, 2)
    out["price_kind"] = _classify_price_kind(out)
    out["sources_checked"] = [c.get("source") for c in candidates]
    out["fetched_at"] = out.get("fetched_at") or _now_iso()
    return out


def fetch_live_quotes_batch(
    tickers: List[str],
    max_workers: int = 16,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    symbols = [t.strip().upper() for t in tickers if t and str(t).strip()]
    if not symbols:
        return out

    with ThreadPoolExecutor(max_workers=max(2, min(max_workers, 24))) as pool:
        futures = {pool.submit(fetch_live_quote, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception as exc:
                out[sym] = {"source": "none", "ok": False, "ticker": sym, "error": str(exc)}
    return out
