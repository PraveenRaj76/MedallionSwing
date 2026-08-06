"""
Live NSE market data provider.

Prices / OHLC / charts  → Yahoo Finance chart API (NSE symbols as TICKER.NS)
Fundamentals            → Screener.in company pages (ROCE, ROE, PE, promoter, growth)
Technicals              → Computed from live OHLCV (SMA50/200, RSI14, ATR14, 3M alpha vs NIFTY)

Set MEDALLION_MARKET_MODE=mock for offline tests.
Set MEDALLION_SSL_VERIFY=0 on corporate SSL-intercept networks (default 0 when verify fails).
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
UNIVERSE_PATH = BASE_DIR / "data" / "nse_universe.txt"
BENCHMARK = "^NSEI"
RSI_OVERBOUGHT = 65.0

# Swing Screener universe = Nifty Midcap 150 + Nifty Smallcap 50 (~200)
NSE_MIDCAP150_CSV = "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv"
NSE_SMALLCAP50_CSV = "https://archives.nseindia.com/content/indices/ind_niftysmallcap50list.csv"
UNIVERSE_LABEL = "Midcap 150 + Smallcap 50"
UNIVERSE_TARGET_HINT = 200

# Market mode: live (default) | mock
MARKET_MODE = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
SSL_VERIFY = os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {"0", "false", "False", "no"}

# Cloud hosts (Streamlit Cloud) need short timeouts — Yahoo often 404s some symbols.
HTTP_TIMEOUT = int(os.environ.get("MEDALLION_HTTP_TIMEOUT", "12"))
_HTTP_LOCK = threading.Lock()
_LAST_REQUEST_TS = 0.0
# Soft rate gap between Yahoo chart calls (seconds). Kept tiny so parallel loads work.
_MIN_GAP_SEC = float(os.environ.get("MEDALLION_HTTP_GAP", "0.02"))

# Liquid bootstrap set — fast first paint after login (prices only, no Screener scrape)
BOOTSTRAP_TICKERS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC",
    "SBIN", "BHARTIARTL", "LT", "AXISBANK", "HCLTECH", "WIPRO",
]

# Yahoo symbol remaps (corporate actions / renamed listings)
YAHOO_ALIASES = {
    "TATAMOTORS": ["TATAMOTORS.NS", "TMPV.NS"],
    "LTIM": ["LTIM.NS", "LTI.NS"],
    "M&M": ["M&M.NS", "M%26M.NS"],
}


def normalize_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    for suffix in (".NS", ".BO", ".NSE"):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    return t.replace(" ", "")


def to_yahoo_symbol(ticker: str) -> str:
    t = normalize_ticker(ticker)
    if t in {"^NSEI", "NSEI", "NIFTY", "NIFTY50"}:
        return "^NSEI"
    return f"{t}.NS"


def yahoo_symbol_candidates(ticker: str) -> List[str]:
    t = normalize_ticker(ticker)
    if t in {"^NSEI", "NSEI", "NIFTY", "NIFTY50"}:
        return ["^NSEI"]
    aliases = YAHOO_ALIASES.get(t)
    if aliases:
        return aliases
    return [f"{t}.NS"]


def load_universe() -> List[str]:
    if UNIVERSE_PATH.exists():
        tickers = []
        for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(normalize_ticker(line))
        if tickers:
            return tickers
    return list(BOOTSTRAP_TICKERS)


def universe_label() -> str:
    return UNIVERSE_LABEL


def _download_nse_index_symbols(csv_url: str) -> List[str]:
    """Download an official NSE index constituent CSV and return Symbol list."""
    from curl_cffi import requests as cr

    resp = cr.get(
        csv_url,
        impersonate="chrome124",
        timeout=max(HTTP_TIMEOUT, 45),
        verify=SSL_VERIFY,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com/",
        },
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"NSE CSV download failed HTTP {resp.status_code}: {csv_url}")

    import csv
    import io

    text = resp.text
    if text.startswith("\ufeff"):
        text = text[1:]
    rows = list(csv.DictReader(io.StringIO(text)))
    symbols: List[str] = []
    for row in rows:
        sym = normalize_ticker(row.get("Symbol") or "")
        if sym:
            symbols.append(sym)
    return symbols


def refresh_swing_universe(*, write_file: bool = True) -> List[str]:
    """
    Build Screener universe: Nifty Midcap 150 ∪ Nifty Smallcap 50.

    Smallcap 50 is NSE's official top-50 liquid smallcaps (preferred over
    alphabetical first-50 of Smallcap 250).
    """
    mid = _download_nse_index_symbols(NSE_MIDCAP150_CSV)
    small = _download_nse_index_symbols(NSE_SMALLCAP50_CSV)
    if len(mid) < 120:
        raise RuntimeError(f"Unexpected Midcap 150 size: {len(mid)}")
    if len(small) < 40:
        raise RuntimeError(f"Unexpected Smallcap 50 size: {len(small)}")

    seen = set()
    symbols: List[str] = []
    for sym in mid + small:
        if sym not in seen:
            seen.add(sym)
            symbols.append(sym)

    if write_file:
        UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Swing universe — {UNIVERSE_LABEL} (official NSE lists)\n"
            f"# Midcap 150: {len(mid)} · Smallcap 50: {len(small)} · "
            f"unique: {len(symbols)}\n"
            f"# Refreshed by nse_data_provider.refresh_swing_universe()\n"
        )
        UNIVERSE_PATH.write_text(header + "\n".join(symbols) + "\n", encoding="utf-8")
        logger.info(
            "Wrote swing universe %s symbols (mid=%s small=%s) → %s",
            len(symbols),
            len(mid),
            len(small),
            UNIVERSE_PATH,
        )
    return symbols


def ensure_swing_universe() -> List[str]:
    """Refresh Mid150+Small50 from NSE; fall back to local file on failure."""
    try:
        return refresh_swing_universe(write_file=True)
    except Exception as exc:
        logger.warning("Swing universe refresh failed (%s) — using local file", exc)
        return load_universe()


def _session_get(url: str, timeout: Optional[int] = None) -> Optional[Any]:
    """HTTP GET via curl_cffi (Chrome impersonation) with polite pacing.

    Lock is only held for rate-limit bookkeeping — never during the network call —
    so parallel ticker loads can actually run concurrently.
    """
    global _LAST_REQUEST_TS, SSL_VERIFY
    timeout = HTTP_TIMEOUT if timeout is None else timeout
    try:
        from curl_cffi import requests as cr
    except ImportError as exc:
        logger.error("curl_cffi missing: %s", exc)
        return None

    def _do(verify_flag: bool):
        return cr.get(
            url,
            impersonate="chrome124",
            timeout=timeout,
            verify=verify_flag,
            headers={
                "Accept": "application/json,text/html,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        )

    # Pace without serializing the full request
    with _HTTP_LOCK:
        gap = time.time() - _LAST_REQUEST_TS
        wait = (_MIN_GAP_SEC - gap) if gap < _MIN_GAP_SEC else 0.0
        if wait <= 0:
            _LAST_REQUEST_TS = time.time()
    if wait > 0:
        time.sleep(wait)
        with _HTTP_LOCK:
            _LAST_REQUEST_TS = time.time()

    try:
        resp = _do(SSL_VERIFY)
        if resp.status_code >= 400:
            logger.warning("HTTP %s for %s", resp.status_code, url[:90])
            return None
        return resp
    except Exception as exc:
        msg = str(exc).lower()
        if SSL_VERIFY and ("ssl" in msg or "certificate" in msg):
            try:
                logger.warning("SSL verify failed — retrying with MEDALLION_SSL_VERIFY=0")
                SSL_VERIFY = False
                resp = _do(False)
                if resp.status_code >= 400:
                    return None
                return resp
            except Exception as exc2:
                logger.warning("HTTP failed %s: %s", url[:90], exc2)
                return None
        logger.warning("HTTP failed %s: %s", url[:90], exc)
        return None


def _parse_chart_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    node = result[0]
    ts = node.get("timestamp") or []
    quote = (node.get("indicators") or {}).get("quote") or [{}]
    q0 = quote[0] if quote else {}
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(ts, unit="s"),
            "open": q0.get("open"),
            "high": q0.get("high"),
            "low": q0.get("low"),
            "close": q0.get("close"),
            "volume": q0.get("volume"),
        }
    )
    frame = frame.dropna(subset=["close"]).reset_index(drop=True)
    frame.attrs["meta"] = node.get("meta") or {}
    return frame


def _fetch_ohlcv_yfinance(ticker: str, range_param: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Secondary path when direct Yahoo chart HTTP is blocked (common on cloud IPs)."""
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()

    period_map = {
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
        "2y": "2y",
        "5y": "5y",
        "max": "max",
    }
    period = period_map.get(range_param, "1y")
    for symbol in yahoo_symbol_candidates(ticker):
        try:
            hist = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
            if hist is None or hist.empty:
                continue
            idx = pd.to_datetime(hist.index)
            try:
                if getattr(idx, "tz", None) is not None:
                    idx = idx.tz_convert(None)
            except Exception:
                idx = pd.to_datetime(hist.index).tz_localize(None)
            frame = pd.DataFrame(
                {
                    "date": idx,
                    "open": hist["Open"].to_numpy(),
                    "high": hist["High"].to_numpy(),
                    "low": hist["Low"].to_numpy(),
                    "close": hist["Close"].to_numpy(),
                    "volume": hist["Volume"].to_numpy() if "Volume" in hist.columns else 0,
                }
            )
            frame = frame.dropna(subset=["close"]).reset_index(drop=True)
            if not frame.empty:
                frame.attrs["meta"] = {"symbol": symbol, "source": "yfinance"}
                return frame
        except Exception as exc:
            logger.warning("yfinance OHLCV failed %s: %s", symbol, exc)
    return pd.DataFrame()


def fetch_ohlcv(ticker: str, range_param: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Fetch NSE daily bars: Yahoo chart HTTP → yfinance → empty (caller may multi-source CMP)."""
    hosts = (
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
    )
    for symbol in yahoo_symbol_candidates(ticker):
        for host in hosts:
            url = (
                f"{host}/v8/finance/chart/{symbol}"
                f"?range={range_param}&interval={interval}&events=div%7Csplit"
            )
            resp = _session_get(url)
            if resp is None:
                continue
            try:
                frame = _parse_chart_payload(resp.json())
                if not frame.empty:
                    return frame
            except Exception as exc:
                logger.error("parse ohlcv failed for %s: %s", symbol, exc)

    yf_frame = _fetch_ohlcv_yfinance(ticker, range_param=range_param, interval=interval)
    if not yf_frame.empty:
        return yf_frame
    return pd.DataFrame()


def _rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    val = float(rsi.iloc[-1])
    return 50.0 if math.isnan(val) else round(val, 2)


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    if len(frame) < period + 1:
        return float(frame["close"].iloc[-1] * 0.02) if len(frame) else 1.0
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.rolling(period).mean().iloc[-1])
    if math.isnan(atr) or atr <= 0:
        atr = float(close.iloc[-1] * 0.02)
    return round(atr, 2)


def _sma(closes: pd.Series, period: int) -> float:
    if len(closes) < period:
        return float(closes.iloc[-1]) if len(closes) else 0.0
    return round(float(closes.tail(period).mean()), 2)


def compute_technicals(frame: pd.DataFrame, bench_frame: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    closes = frame["close"].astype(float)
    close = float(closes.iloc[-1])
    sma_50 = _sma(closes, 50)
    sma_200 = _sma(closes, 200)
    rsi_14 = _rsi(closes, 14)
    atr_value = _atr(frame, 14)

    alpha_3m = 0.0
    lookback = min(63, len(closes) - 1)
    if lookback > 5:
        stock_ret = (close / float(closes.iloc[-lookback - 1]) - 1.0) * 100.0
        if bench_frame is not None and len(bench_frame) > lookback:
            b = bench_frame["close"].astype(float)
            bench_ret = (float(b.iloc[-1]) / float(b.iloc[-lookback - 1]) - 1.0) * 100.0
            alpha_3m = round(stock_ret - bench_ret, 2)
        else:
            alpha_3m = round(stock_ret, 2)

    # Delivery % is not on Yahoo; approximate institutional interest via volume z-score → 30–70 band
    volumes = frame["volume"].astype(float).fillna(0.0)
    if len(volumes) >= 20 and float(volumes.tail(20).mean()) > 0:
        z = (float(volumes.iloc[-1]) - float(volumes.tail(20).mean())) / max(
            float(volumes.tail(20).std(ddof=0)), 1.0
        )
        delivery_pct = float(np.clip(45.0 + z * 6.0, 25.0, 75.0))
    else:
        delivery_pct = 45.0

    return {
        "close_price": round(close, 2),
        "sma_50": sma_50,
        "sma_200": sma_200,
        "rsi_14": rsi_14,
        "atr_value": atr_value,
        "alpha_3m": alpha_3m,
        "delivery_pct_10d": round(delivery_pct, 1),
    }


def _parse_number(text: str) -> Optional[float]:
    if text is None:
        return None
    cleaned = str(text)
    cleaned = cleaned.replace(",", "").replace("%", "").replace("₹", "").strip()
    cleaned = cleaned.replace("Cr.", "").replace("Cr", "").strip()
    # High/Low like "3350 / 1976"
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0].strip()
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def fetch_fundamentals_screener(ticker: str) -> Dict[str, Any]:
    """
    Scrape Screener.in consolidated page for fundamental ratios.
    Returns empty dict on failure (caller keeps prior DB values / defaults).
    """
    symbol = normalize_ticker(ticker)
    # Screener uses URL-safe symbols; M&M → M%26M, BAJAJ-AUTO stays
    slug = symbol.replace("&", "%26")
    url = f"https://www.screener.in/company/{slug}/consolidated/"
    resp = _session_get(url, timeout=15)
    if resp is None:
        # some tickers only have standalone pages
        resp = _session_get(f"https://www.screener.in/company/{slug}/", timeout=15)
    if resp is None:
        return {}

    html = resp.text
    ratios: Dict[str, float] = {}
    items = re.findall(
        r'<span class="name">\s*(.*?)\s*</span>\s*<span class="nowrap value">(.*?)</span>',
        html,
        re.S,
    )
    for name_html, val_html in items:
        name = re.sub(r"<.*?>", "", name_html).strip().lower()
        val = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", val_html)).strip()
        num = _parse_number(val)
        if num is None:
            continue
        ratios[name] = num

    roce = ratios.get("roce")
    roe = ratios.get("roe")
    pe = ratios.get("stock p/e") or ratios.get("p/e")

    promoter = None
    m_prom = re.search(r"Promoter Holding[:\s]*([\d.]+)\s*%", html, re.I)
    if m_prom:
        promoter = float(m_prom.group(1))

    profit_growth = None
    m_pg = re.search(
        r"Profit Growth</th>\s*</tr>\s*<tr>\s*<td>10 Years:</td>\s*<td>(-?[\d.]+)%?</td>",
        html,
        re.I | re.S,
    )
    if not m_pg:
        m_pg = re.search(
            r"<td>5 Years:</td>\s*<td>(-?[\d.]+)%?</td>",
            html,
            re.I | re.S,
        )
    if m_pg:
        profit_growth = float(m_pg.group(1))

    # Sales growth from meta blurb if present
    m_sg = re.search(r"sales growth of\s*(-?[\d.]+)%", html, re.I)
    sales_growth = float(m_sg.group(1)) if m_sg else None

    sector = industry = None
    m_sec = re.search(r'Sector">\s*([^<]+?)\s*</a>', html, re.I)
    if m_sec:
        sector = m_sec.group(1).strip()
    m_ind = re.search(r'Industry">\s*([^<]+?)\s*</a>', html, re.I)
    if m_ind:
        industry = m_ind.group(1).strip()

    title = None
    m_title = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.I | re.S)
    if m_title:
        title = re.sub(r"<.*?>", "", m_title.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        # strip share price suffix
        title = re.sub(r"\s+share price.*$", "", title, flags=re.I).strip()

    desc = ""
    m_about = re.search(
        r'<div class="about"[^>]*>.*?<p[^>]*>(.*?)</p>',
        html,
        re.I | re.S,
    )
    if m_about:
        desc = re.sub(r"<.*?>", "", m_about.group(1))
        desc = re.sub(r"\s+", " ", desc).strip()[:320]

    # PEG ≈ PE / growth when growth > 0
    growth_for_peg = profit_growth if profit_growth and profit_growth > 0 else sales_growth
    peg = None
    if pe and growth_for_peg and growth_for_peg > 0:
        peg = round(pe / growth_for_peg, 2)

    # Never invent debt / coverage / PEG / ROCE defaults — missing stays None
    return {
        "company_name": title or symbol,
        "description": desc or f"NSE-listed equity {symbol}.",
        "sector": sector or "—",
        "industry": industry or "—",
        "roic": round(float(roce), 2) if roce is not None else None,
        "roe": round(float(roe), 2) if roe is not None else None,
        "peg_ratio": float(peg) if peg is not None else None,
        "net_debt_ebitda": None,
        "interest_coverage": None,
        "promoter_pledge_pct": 0.0 if promoter is not None else None,
        "promoter_holding_pct": float(promoter) if promoter is not None else None,
        "yoy_profit_growth": float(profit_growth) if profit_growth is not None else (
            float(sales_growth) if sales_growth is not None else None
        ),
        "pe_ratio": float(pe) if pe is not None else None,
    }


def _score_fundamental(row: Dict[str, Any]) -> float:
    try:
        import factor_engine as fe

        return float(fe.evaluate_fundamental_checklist(row)["total_marks"])
    except Exception:
        return 0.0


def _score_technical(row: Dict[str, Any]) -> float:
    try:
        import factor_engine as fe

        return float(fe.evaluate_technical_checklist(row)["total_marks"])
    except Exception:
        return 0.0


def _fetch_fundamentals_safe(ticker: str) -> Dict[str, Any]:
    try:
        import multi_source_data as msd

        return msd.fetch_verified_fundamentals(ticker)
    except Exception as exc:
        logger.warning("multi-source fundamentals failed for %s: %s", ticker, exc)
        return {"data_quality": "UNVERIFIED", "fundamentals_verified": False}


def build_live_row(
    ticker: str,
    bench_frame: Optional[pd.DataFrame] = None,
    include_fundamentals: bool = True,
    prior: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    ticker = normalize_ticker(ticker)
    prior = prior or {}

    fund: Dict[str, Any] = {}
    # OHLCV + fundamentals in parallel (biggest per-ticker speedup)
    if include_fundamentals:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_hist = pool.submit(fetch_ohlcv, ticker, "1y", "1d")
            fut_fund = pool.submit(_fetch_fundamentals_safe, ticker)
            hist = fut_hist.result()
            fund = fut_fund.result() or {}
    else:
        hist = fetch_ohlcv(ticker, range_param="1y", interval="1d")

    # Fallback when Yahoo chart is blocked: still build a row from Tickertape/MC/Screener CMP
    ohlcv_ready = False
    if hist is None or getattr(hist, "empty", True):
        logger.warning("Yahoo OHLCV empty for %s — trying multi-source price fallback", ticker)
        px = None
        if fund.get("close_price"):
            px = float(fund["close_price"])
        else:
            try:
                import multi_source_data as msd

                tt = msd.fetch_tickertape(ticker)
                mc = msd.fetch_moneycontrol(ticker)
                px = tt.get("close_price") or mc.get("close_price")
                if not fund.get("company_name") and tt.get("company_name"):
                    fund["company_name"] = tt.get("company_name")
                    fund["sector"] = tt.get("sector") or fund.get("sector")
            except Exception as exc:
                logger.warning("price fallback failed for %s: %s", ticker, exc)
        if px is None and prior.get("close_price"):
            px = float(prior["close_price"])
        if px is None:
            return None
        # Minimal technicals from prior — do NOT invent buyable RSI/SMA scores
        if prior.get("ohlcv_ready") and prior.get("sma_200"):
            tech = {
                "close_price": round(float(px), 2),
                "sma_50": float(prior.get("sma_50") or px),
                "sma_200": float(prior.get("sma_200") or px),
                "rsi_14": float(prior.get("rsi_14") or 50.0),
                "atr_value": float(prior.get("atr_value") or max(px * 0.02, 0.05)),
                "alpha_3m": float(prior.get("alpha_3m") or 0.0),
                "delivery_pct_10d": float(prior.get("delivery_pct_10d") or 45.0),
            }
            ohlcv_ready = True
        else:
            tech = {
                "close_price": round(float(px), 2),
                "sma_50": round(float(px), 2),
                "sma_200": round(float(px), 2),
                "rsi_14": 50.0,
                "atr_value": float(prior.get("atr_value") or max(px * 0.02, 0.05)),
                "alpha_3m": 0.0,
                "delivery_pct_10d": 0.0,
            }
        meta = {}
        if not fund.get("description"):
            fund["description"] = (
                "Price from multi-source (Tickertape/Moneycontrol/Screener); "
                "technicals pending until Yahoo OHLCV returns."
            )
    else:
        tech = compute_technicals(hist, bench_frame)
        meta = hist.attrs.get("meta") or {}
        ohlcv_ready = True

    company_name = (
        fund.get("company_name")
        or meta.get("longName")
        or meta.get("shortName")
        or (prior or {}).get("company_name")
        or ticker
    )
    description = fund.get("description") or (prior or {}).get("description") or f"NSE equity {company_name}."
    sector = fund.get("sector") or (prior or {}).get("sector") or "—"
    industry = fund.get("industry") or (prior or {}).get("industry") or "—"

    def pick_optional(key: str) -> Optional[float]:
        """Only trust multi-source consensus / explicit values — NEVER invent defaults."""
        if key in fund and fund[key] is not None:
            try:
                return float(fund[key])
            except (TypeError, ValueError):
                return None
        return None

    # Price: Yahoo technicals primary; override only if multi-source CMP verified and close
    close_price = tech["close_price"]
    ms_px = pick_optional("close_price")
    if ms_px and abs(ms_px - close_price) / max(close_price, 1e-9) < 0.08:
        close_price = round((close_price + ms_px) / 2.0, 2)

    quality = str(fund.get("data_quality") or "UNVERIFIED")
    verified = bool(fund.get("fundamentals_verified"))

    row: Dict[str, Any] = {
        "ticker": ticker,
        "company_name": company_name,
        "description": description,
        "sector": sector,
        "industry": industry,
        "close_price": close_price,
        "atr_value": tech["atr_value"],
        "sma_50": tech["sma_50"],
        "sma_200": tech["sma_200"],
        "rsi_14": tech["rsi_14"],
        "delivery_pct_10d": tech["delivery_pct_10d"],
        "alpha_3m": tech["alpha_3m"],
        "roic": pick_optional("roic"),
        "roe": pick_optional("roe"),
        "net_debt_ebitda": pick_optional("net_debt_ebitda"),
        "peg_ratio": pick_optional("peg_ratio"),
        "interest_coverage": pick_optional("interest_coverage"),
        "promoter_pledge_pct": pick_optional("promoter_pledge_pct"),
        "yoy_profit_growth": pick_optional("yoy_profit_growth"),
        "pe_ratio": pick_optional("pe_ratio"),
        "promoter_holding_pct": fund.get("promoter_holding_pct"),
        "fundamentals_verified": verified,
        "data_quality": quality,
        "fundamentals_sources": fund.get("fundamentals_sources") or [],
        "sources_ok_count": int(
            fund.get("sources_ok_count")
            or len(fund.get("fundamentals_sources") or [])
        ),
        "fundamentals_report": fund.get("fundamentals_report"),
        "ohlcv_ready": bool(ohlcv_ready),
    }
    for key in (
        "roic",
        "roe",
        "net_debt_ebitda",
        "peg_ratio",
        "interest_coverage",
        "promoter_pledge_pct",
        "yoy_profit_growth",
        "pe_ratio",
    ):
        if row[key] is None:
            row[key] = None

    row["fundamental_score"] = _score_fundamental(row)
    row["technical_score"] = _score_technical(row) if ohlcv_ready else 0.0
    row["composite_score"] = round(float(row["fundamental_score"]) + float(row["technical_score"]), 1)
    row["is_buyable"] = (
        1
        if ohlcv_ready
        and row["close_price"] > row["sma_200"]
        and row["rsi_14"] <= RSI_OVERBOUGHT
        else 0
    )
    return row


def refresh_universe_live(
    tickers: Optional[List[str]] = None,
    max_workers: int = 6,
    include_fundamentals: bool = True,
    deadline_sec: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Refresh screener universe from live NSE feeds.
    deadline_sec: hard stop so Streamlit Cloud login never hangs forever.
    """
    tickers = tickers or load_universe()
    started = time.time()
    bench = fetch_ohlcv(BENCHMARK, range_param="6mo", interval="1d")
    rows: List[Dict[str, Any]] = []

    def _one(sym: str) -> Optional[Dict[str, Any]]:
        try:
            return build_live_row(sym, bench_frame=bench, include_fundamentals=include_fundamentals)
        except Exception as exc:
            logger.error("live row failed %s: %s", sym, exc)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, t): t for t in tickers}
        for fut in as_completed(futures):
            if deadline_sec is not None and (time.time() - started) >= deadline_sec:
                logger.warning(
                    "Universe refresh hit deadline (%.0fs) — returning %s rows",
                    deadline_sec,
                    len(rows),
                )
                for pending in futures:
                    pending.cancel()
                break
            try:
                row = fut.result(timeout=1)
            except Exception:
                continue
            if row:
                rows.append(row)

    rows.sort(key=lambda r: r.get("composite_score", 0), reverse=True)
    return rows


def fetch_chart_history(ticker: str, periods: int = 250) -> pd.DataFrame:
    """OHLCV for Plotly charts — live NSE via Yahoo."""
    frame = fetch_ohlcv(ticker, range_param="1y", interval="1d")
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if len(frame) > periods:
        frame = frame.tail(periods).reset_index(drop=True)
    return frame


def is_live_mode() -> bool:
    return MARKET_MODE not in {"mock", "offline", "test"}
