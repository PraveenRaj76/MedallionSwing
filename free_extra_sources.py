"""
Extra free fundamentals sources — BSE company meta and NSE corporate filings.

BSE (api.bseindia.com)   -> EPS, P/E, ROE, P/B, sector, industry, results history
NSE (www.nseindia.com)   -> promoter holding %, promoter pledge %

These are the same public JSON endpoints bseindia.com / nseindia.com serve to
their own web front-ends. No API key, no scraping of paid content, and no
value is ever invented — a metric is either read from the response or left None.

Speed notes:
  * the BSE symbol -> scripcode map is one call for ~5k stocks, cached on disk
  * per-ticker responses are memoised for the rest of the trading day
  * NSE needs a cookie-primed session; one is kept per worker thread
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
SCRIP_MAP_PATH = BASE_DIR / "data" / "bse_scrip_map.json"
SCRIP_MAP_MAX_AGE_SEC = 7 * 24 * 3600

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_REFERER = "https://www.bseindia.com/"
NSE_BASE = "https://www.nseindia.com"
NSE_REFERER = "https://www.nseindia.com/"

HTTP_TIMEOUT = int(os.environ.get("MEDALLION_EXTRA_TIMEOUT", "20"))
CACHE_TTL_SEC = int(os.environ.get("MEDALLION_EXTRA_CACHE_TTL", "21600"))  # 6h

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_scrip_lock = threading.Lock()
_scrip_map: Optional[Dict[str, Dict[str, str]]] = None

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {}

_nse_local = threading.local()


def _ssl_verify() -> bool:
    return os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {"0", "false", "False", "no"}


def _num(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return None if val != val else float(val)
    text = str(val).replace(",", "").replace("%", "").strip()
    if not text or text.upper() in {"-", "--", "NA", "N/A", "NULL", "NONE"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _positive(val: Optional[float]) -> Optional[float]:
    """BSE returns 0.00 for 'not reported' on consolidated fields."""
    return val if val is not None and val > 0 else None


def _cache_get(key: str) -> Any:
    with _cache_lock:
        hit = _cache.get(key)
    if not hit:
        return None
    value, stamp = hit
    if time.time() - stamp > CACHE_TTL_SEC:
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (value, time.time())


def _get(url: str, referer: str, timeout: Optional[int] = None, session: Any = None):
    from curl_cffi import requests as cr

    caller = session if session is not None else cr
    try:
        return caller.get(
            url,
            impersonate="chrome124",
            timeout=timeout or HTTP_TIMEOUT,
            verify=_ssl_verify(),
            headers={
                "User-Agent": _UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": referer,
            },
        )
    except Exception as exc:
        logger.debug("free-extra GET failed %s: %s", url[:90], exc)
        return None


def _json_body(resp: Any) -> Any:
    if resp is None or resp.status_code >= 400:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    # BSE occasionally double-encodes its JSON payloads
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return None
    return body


# --------------------------------------------------------------------------- BSE


def _load_scrip_map_from_disk() -> Optional[Dict[str, Dict[str, str]]]:
    if not SCRIP_MAP_PATH.exists():
        return None
    if time.time() - SCRIP_MAP_PATH.stat().st_mtime > SCRIP_MAP_MAX_AGE_SEC:
        return None
    try:
        data = json.loads(SCRIP_MAP_PATH.read_text(encoding="utf-8"))
        if data.get("by_symbol"):
            return data
    except Exception as exc:
        logger.warning("bse scrip map read failed: %s", exc)
    return None


def _download_scrip_map() -> Optional[Dict[str, Dict[str, str]]]:
    resp = _get(
        f"{BSE_API}/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active",
        BSE_REFERER,
        timeout=60,
    )
    rows = _json_body(resp)
    if not isinstance(rows, list) or len(rows) < 500:
        logger.warning("BSE scrip list unavailable (rows=%s)", 0 if not rows else len(rows))
        return None

    by_symbol: Dict[str, str] = {}
    by_isin: Dict[str, str] = {}
    for row in rows:
        code = str(row.get("SCRIP_CD") or "").strip()
        if not code:
            continue
        symbol = str(row.get("scrip_id") or "").strip().upper()
        isin = str(row.get("ISIN_NUMBER") or "").strip().upper()
        if symbol:
            by_symbol.setdefault(symbol, code)
        if isin:
            by_isin.setdefault(isin, code)

    payload = {"by_symbol": by_symbol, "by_isin": by_isin}
    try:
        SCRIP_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCRIP_MAP_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:
        logger.warning("bse scrip map write failed: %s", exc)
    logger.info("BSE scrip map built: %s symbols", len(by_symbol))
    return payload


def scrip_map() -> Dict[str, Dict[str, str]]:
    global _scrip_map
    if _scrip_map is not None:
        return _scrip_map
    with _scrip_lock:
        if _scrip_map is not None:
            return _scrip_map
        _scrip_map = _load_scrip_map_from_disk() or _download_scrip_map() or {"by_symbol": {}, "by_isin": {}}
        return _scrip_map


def bse_scripcode(symbol: str, isin: Optional[str] = None) -> Optional[str]:
    mapping = scrip_map()
    sym = (symbol or "").strip().upper()
    code = mapping.get("by_symbol", {}).get(sym)
    if code:
        return code
    if isin:
        return mapping.get("by_isin", {}).get(isin.strip().upper())
    return None


def fetch_bse(ticker: str, isin: Optional[str] = None) -> Dict[str, Any]:
    """BSE company header — EPS, P/E, ROE, P/B, sector, industry."""
    symbol = (ticker or "").strip().upper()
    cache_key = f"bse:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    out: Dict[str, Any] = {"source": "bse", "ok": False}
    code = bse_scripcode(symbol, isin)
    if not code:
        _cache_put(cache_key, out)
        return out

    body = _json_body(
        _get(f"{BSE_API}/ComHeadernew/w?quotetype=EQ&scripcode={code}&seriesid=", BSE_REFERER)
    )
    if not isinstance(body, dict):
        _cache_put(cache_key, out)
        return out

    # Consolidated figures are the better read when the company reports them
    pe = _positive(_num(body.get("ConPE"))) or _positive(_num(body.get("PE")))
    eps = _positive(_num(body.get("ConEPS"))) or _positive(_num(body.get("EPS")))
    roe = _positive(_num(body.get("ConROE"))) or _positive(_num(body.get("ROE")))

    out["ok"] = pe is not None or roe is not None or eps is not None
    out["scripcode"] = code
    out["pe_ratio"] = pe
    out["roe"] = roe
    out["eps"] = eps
    out["book_value"] = _positive(_num(body.get("ConPB")) or _num(body.get("PB")))
    out["sector"] = (body.get("Sector") or "").strip() or None
    out["industry"] = (body.get("Industry") or "").strip() or None
    out["company_name"] = (body.get("COName") or body.get("Comp_Name") or "").strip() or None
    # BSE header carries no ROCE / leverage figures — never guess them
    out["roic"] = None
    out["peg_ratio"] = None
    out["yoy_profit_growth"] = None
    out["net_debt_ebitda"] = None
    out["interest_coverage"] = None

    _cache_put(cache_key, out)
    return out


# --------------------------------------------------------------------------- NSE


def _nse_session():
    session = getattr(_nse_local, "session", None)
    if session is not None:
        return session
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome124")
    try:
        session.get(NSE_BASE, timeout=HTTP_TIMEOUT, verify=_ssl_verify(), headers={"User-Agent": _UA})
    except Exception as exc:
        logger.debug("NSE cookie prime failed: %s", exc)
    _nse_local.session = session
    return session


def _nse_pledge(symbol: str, session: Any) -> Dict[str, Optional[float]]:
    """Promoter pledge % straight from NSE's SAST encumbrance disclosure."""
    body = _json_body(
        _get(
            f"{NSE_BASE}/api/corporate-pledgedata?index=equities&symbol={symbol}",
            NSE_REFERER,
            session=session,
        )
    )
    out: Dict[str, Optional[float]] = {"promoter_pledge_pct": None, "promoter_holding_pct": None}
    if not isinstance(body, dict):
        return out
    rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        return out
    latest = rows[0]
    out["promoter_pledge_pct"] = _num(latest.get("percSharesPledged"))
    out["promoter_holding_pct"] = _num(latest.get("percPromoterHolding"))
    return out


def fetch_nse_filings(ticker: str) -> Dict[str, Any]:
    """NSE corporate filings — promoter holding % and promoter pledge %."""
    symbol = (ticker or "").strip().upper()
    cache_key = f"nsefil:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    out: Dict[str, Any] = {"source": "nse_filings", "ok": False}
    session = _nse_session()

    holdings = _json_body(
        _get(
            f"{NSE_BASE}/api/corporate-share-holdings-master?index=equities&symbol={symbol}",
            NSE_REFERER,
            session=session,
        )
    )
    if isinstance(holdings, list) and holdings:
        latest = holdings[0]
        promoter = _num(latest.get("pr_and_prgrp"))
        if promoter is not None:
            out["ok"] = True
            out["promoter_holding_pct"] = promoter
            out["company_name"] = (latest.get("name") or "").strip() or None
            out["isin"] = (latest.get("isin") or "").strip() or None

    pledge = _nse_pledge(symbol, session)
    if pledge["promoter_pledge_pct"] is not None:
        out["ok"] = True
        out["promoter_pledge_pct"] = pledge["promoter_pledge_pct"]
        if out.get("promoter_holding_pct") is None:
            out["promoter_holding_pct"] = pledge["promoter_holding_pct"]
    elif out.get("promoter_holding_pct") == 0:
        # No promoter block on record — nothing can be pledged
        out["promoter_pledge_pct"] = 0.0

    _cache_put(cache_key, out)
    return out


def fetch_extras(ticker: str, isin: Optional[str] = None) -> List[Dict[str, Any]]:
    """Both extra sources in parallel — used by the multi-source consensus."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_bse = pool.submit(fetch_bse, ticker, isin)
        fut_nse = pool.submit(fetch_nse_filings, ticker)
        return [fut_bse.result(), fut_nse.result()]


def warm_up() -> None:
    """Pre-build the BSE scrip map so the first parallel batch is not serialised."""
    try:
        scrip_map()
    except Exception as exc:
        logger.warning("free-extra warm-up failed: %s", exc)
