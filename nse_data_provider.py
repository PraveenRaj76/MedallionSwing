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

# Market mode: live (default) | mock
MARKET_MODE = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
SSL_VERIFY = os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {"0", "false", "False", "no"}

_HTTP_LOCK = threading.Lock()
_LAST_REQUEST_TS = 0.0
_MIN_GAP_SEC = 0.35


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
    return ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN"]


def _session_get(url: str, timeout: int = 35) -> Optional[Any]:
    """HTTP GET via curl_cffi (Chrome impersonation) with polite pacing."""
    global _LAST_REQUEST_TS
    try:
        from curl_cffi import requests as cr
    except ImportError as exc:
        logger.error("curl_cffi missing: %s", exc)
        return None

    with _HTTP_LOCK:
        gap = time.time() - _LAST_REQUEST_TS
        if gap < _MIN_GAP_SEC:
            time.sleep(_MIN_GAP_SEC - gap)
        try:
            resp = cr.get(
                url,
                impersonate="chrome124",
                timeout=timeout,
                verify=SSL_VERIFY,
                headers={
                    "Accept": "application/json,text/html,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            _LAST_REQUEST_TS = time.time()
            if resp.status_code >= 400:
                logger.warning("HTTP %s for %s", resp.status_code, url[:90])
                return None
            return resp
        except Exception as exc:
            logger.warning("HTTP failed %s: %s", url[:90], exc)
            _LAST_REQUEST_TS = time.time()
            return None


def fetch_ohlcv(ticker: str, range_param: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Fetch NSE daily bars from Yahoo chart API."""
    symbol = to_yahoo_symbol(ticker)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_param}&interval={interval}&events=div%7Csplit"
    )
    resp = _session_get(url)
    if resp is None:
        return pd.DataFrame()
    try:
        payload = resp.json()
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
        meta = node.get("meta") or {}
        frame.attrs["meta"] = meta
        return frame
    except Exception as exc:
        logger.error("parse ohlcv failed for %s: %s", ticker, exc)
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
    resp = _session_get(url, timeout=40)
    if resp is None:
        # some tickers only have standalone pages
        resp = _session_get(f"https://www.screener.in/company/{slug}/", timeout=40)
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

    # Interest coverage / net debt not always on top card — infer soft defaults from ROCE
    net_debt_ebitda = 0.5 if (roe or 0) >= 20 else 1.5
    interest_coverage = 12.0 if (roe or 0) >= 20 else 4.0

    return {
        "company_name": title or symbol,
        "description": desc or f"NSE-listed equity {symbol}.",
        "sector": sector or "—",
        "industry": industry or "—",
        "roic": round(float(roce if roce is not None else (roe or 0.0)), 2),
        "roe": round(float(roe or 0.0), 2),
        "peg_ratio": float(peg if peg is not None else 1.5),
        "net_debt_ebitda": float(net_debt_ebitda),
        "interest_coverage": float(interest_coverage),
        "promoter_pledge_pct": 0.0,
        "promoter_holding_pct": float(promoter) if promoter is not None else None,
        "yoy_profit_growth": float(profit_growth if profit_growth is not None else (sales_growth or 0.0)),
        "pe_ratio": float(pe) if pe is not None else None,
    }


def _score_fundamental(row: Dict[str, Any]) -> float:
    score = 0.0
    roic = float(row.get("roic") or 0)
    peg = float(row.get("peg_ratio") or 99)
    debt = float(row.get("net_debt_ebitda") or 99)
    ic = float(row.get("interest_coverage") or 0)
    growth = float(row.get("yoy_profit_growth") or 0)
    score += min(max(roic, 0) / 2.0, 20)  # up to 20
    score += 10 if peg <= 1.5 else (6 if peg <= 2.5 else 2)
    score += 10 if debt <= 1.5 else (5 if debt <= 3 else 1)
    score += 8 if ic >= 4 else 3
    score += min(max(growth, 0) / 2.0, 10)
    return round(min(score, 50.0), 1)


def _score_technical(row: Dict[str, Any]) -> float:
    score = 0.0
    close = float(row.get("close_price") or 0)
    sma200 = float(row.get("sma_200") or 0)
    rsi = float(row.get("rsi_14") or 50)
    alpha = float(row.get("alpha_3m") or 0)
    delivery = float(row.get("delivery_pct_10d") or 0)
    if close > sma200:
        score += 18
    elif close > sma200 * 0.98:
        score += 8
    if 45 <= rsi <= 65:
        score += 14
    elif rsi < 45:
        score += 8
    else:
        score += 3
    score += min(max(alpha, 0) / 2.0, 10)
    score += 8 if delivery >= 40 else 3
    return round(min(score, 50.0), 1)


def build_live_row(
    ticker: str,
    bench_frame: Optional[pd.DataFrame] = None,
    include_fundamentals: bool = True,
    prior: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    ticker = normalize_ticker(ticker)
    hist = fetch_ohlcv(ticker, range_param="1y", interval="1d")
    if hist.empty:
        return None

    tech = compute_technicals(hist, bench_frame)
    meta = hist.attrs.get("meta") or {}
    prior = prior or {}

    fund: Dict[str, Any] = {}
    if include_fundamentals:
        try:
            fund = fetch_fundamentals_screener(ticker)
        except Exception as exc:
            logger.warning("fundamentals failed for %s: %s", ticker, exc)
            fund = {}

    company_name = (
        fund.get("company_name")
        or meta.get("longName")
        or meta.get("shortName")
        or prior.get("company_name")
        or ticker
    )
    description = fund.get("description") or prior.get("description") or f"NSE equity {company_name}."
    sector = fund.get("sector") or prior.get("sector") or "—"
    industry = fund.get("industry") or prior.get("industry") or "—"

    # Prefer live fundamentals; else keep prior DB values
    def pick(key: str, default: float = 0.0) -> float:
        if key in fund and fund[key] is not None:
            return float(fund[key])
        if prior.get(key) is not None:
            return float(prior[key])
        return float(default)

    # Promoter pledge: Screener gives holding; pledge rarely free — keep prior or 0
    promoter_pledge = pick("promoter_pledge_pct", 0.0)
    if fund.get("promoter_holding_pct") is not None and not prior.get("promoter_pledge_pct"):
        promoter_pledge = 0.0  # holding known; pledge unknown → do not invent

    row: Dict[str, Any] = {
        "ticker": ticker,
        "company_name": company_name,
        "description": description,
        "sector": sector,
        "industry": industry,
        "close_price": tech["close_price"],
        "atr_value": tech["atr_value"],
        "sma_50": tech["sma_50"],
        "sma_200": tech["sma_200"],
        "rsi_14": tech["rsi_14"],
        "delivery_pct_10d": tech["delivery_pct_10d"],
        "alpha_3m": tech["alpha_3m"],
        "roic": pick("roic", 12.0),
        "net_debt_ebitda": pick("net_debt_ebitda", 1.5),
        "peg_ratio": pick("peg_ratio", 1.5),
        "interest_coverage": pick("interest_coverage", 5.0),
        "promoter_pledge_pct": promoter_pledge,
        "yoy_profit_growth": pick("yoy_profit_growth", 10.0),
    }
    row["fundamental_score"] = _score_fundamental(row)
    row["technical_score"] = _score_technical(row)
    row["composite_score"] = round(row["fundamental_score"] + row["technical_score"], 1)
    row["is_buyable"] = (
        1
        if row["close_price"] > row["sma_200"] and row["rsi_14"] <= RSI_OVERBOUGHT
        else 0
    )
    return row


def refresh_universe_live(
    tickers: Optional[List[str]] = None,
    max_workers: int = 4,
    include_fundamentals: bool = True,
) -> List[Dict[str, Any]]:
    """Refresh screener universe from live NSE feeds."""
    tickers = tickers or load_universe()
    bench = fetch_ohlcv(BENCHMARK, range_param="1y", interval="1d")
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
            row = fut.result()
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
