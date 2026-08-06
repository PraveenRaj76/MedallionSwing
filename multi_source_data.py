"""
Multi-source market fundamentals with consensus checks.

Sources (free):
  1) Screener.in  — ROCE, ROE, PE, growth, sector
  2) Tickertape   — PE, ROE, price, sector
  3) Moneycontrol — price, consensus PE (PECONS)

A metric is ACCEPTED only when ≥2 sources agree (within tolerance),
except ROCE (usually Screener-only) which is accepted as single-source
but flagged — never invented from defaults.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Relative tolerance for agreement (e.g. 0.20 = 20%)
REL_TOL = 0.25
# Absolute floor for small percentages
ABS_TOL_PCT = 1.5


def _num(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if val != val:  # NaN
            return None
        return float(val)
    text = str(val).replace(",", "").replace("%", "").replace("₹", "").strip()
    if not text or text in {"--", "-", "NA", "N/A", "null"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _agree(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) <= max(ABS_TOL_PCT, REL_TOL * scale)


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        raise ValueError("empty")
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _session_get(url: str, referer: str = "", timeout: int = 18):
    import os

    from curl_cffi import requests as cr

    verify = os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {
        "0",
        "false",
        "False",
        "no",
    }
    try:
        return cr.get(
            url,
            impersonate="chrome124",
            timeout=timeout,
            verify=verify,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/html,*/*",
                "Referer": referer or url,
            },
        )
    except Exception as exc:
        logger.warning("multi-source GET failed %s: %s", url[:80], exc)
        return None


def _screener_profit_growth(html: str) -> Optional[float]:
    """
    Compounded profit growth from Screener's ranges table.

    Longest published window first; the shorter ones matter for recently listed
    companies (demergers, new IPOs) that have no 5/10-year history at all.
    """
    block = re.search(
        r"Profit\s+Growth\s*</th>(.*?)</table>",
        html,
        re.I | re.S,
    )
    if not block:
        return None
    periods = dict(
        (label.strip().lower(), value)
        for label, value in re.findall(
            r"<td>\s*([^<]+?)\s*:?\s*</td>\s*<td>\s*(-?[\d.,]+)\s*%?\s*</td>",
            block.group(1),
            re.I | re.S,
        )
    )
    for key in ("10 years", "5 years", "3 years", "ttm", "1 year"):
        val = _num(periods.get(key))
        if val is not None:
            return val
    return None


def fetch_screener(ticker: str) -> Dict[str, Any]:
    symbol = ticker.strip().upper()
    slug = symbol.replace("&", "%26")
    out: Dict[str, Any] = {"source": "screener"}
    resp = _session_get(
        f"https://www.screener.in/company/{slug}/consolidated/",
        "https://www.screener.in/",
    )
    if resp is None or resp.status_code >= 400:
        resp = _session_get(
            f"https://www.screener.in/company/{slug}/",
            "https://www.screener.in/",
        )
    if resp is None or resp.status_code >= 400:
        out["ok"] = False
        return out

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
        num = _num(val)
        if num is not None:
            ratios[name] = num

    out["ok"] = True
    out["close_price"] = ratios.get("current price")
    out["pe_ratio"] = ratios.get("stock p/e") or ratios.get("p/e")
    out["roic"] = ratios.get("roce")
    out["roe"] = ratios.get("roe")
    out["book_value"] = ratios.get("book value")

    m_prom = re.search(r"Promoter Holding[:\s]*([\d.]+)\s*%", html, re.I)
    out["promoter_holding_pct"] = float(m_prom.group(1)) if m_prom else None

    out["yoy_profit_growth"] = _screener_profit_growth(html)

    m_sec = re.search(r'Sector">\s*([^<]+?)\s*</a>', html, re.I)
    m_ind = re.search(r'Industry">\s*([^<]+?)\s*</a>', html, re.I)
    out["sector"] = m_sec.group(1).strip() if m_sec else None
    out["industry"] = m_ind.group(1).strip() if m_ind else None

    m_title = re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.I | re.S)
    if m_title:
        title = re.sub(r"<.*?>", "", m_title.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        out["company_name"] = re.sub(r"\s+share price.*$", "", title, flags=re.I).strip()

    m_about = re.search(r'<div class="about"[^>]*>.*?<p[^>]*>(.*?)</p>', html, re.I | re.S)
    if m_about:
        out["description"] = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", m_about.group(1))).strip()[:320]

    # Never invent debt / coverage / PEG here
    pe, growth = out.get("pe_ratio"), out.get("yoy_profit_growth")
    if pe and growth and growth > 0:
        out["peg_ratio"] = round(pe / growth, 2)
    else:
        out["peg_ratio"] = None
    out["net_debt_ebitda"] = None
    out["interest_coverage"] = None
    out["promoter_pledge_pct"] = 0.0 if out.get("promoter_holding_pct") is not None else None
    return out


def fetch_tickertape(ticker: str) -> Dict[str, Any]:
    symbol = ticker.strip().upper()
    out: Dict[str, Any] = {"source": "tickertape", "ok": False}
    url = f"https://api.tickertape.in/stocks/info/{symbol}"
    resp = None
    for attempt in range(2):
        resp = _session_get(url, "https://www.tickertape.in/", timeout=12)
        if resp is not None and resp.status_code < 400:
            break
        if attempt == 0:
            time.sleep(0.15)
    if resp is None or resp.status_code >= 400:
        return out
    try:
        payload = resp.json()
        data = (payload.get("data") or {})
        info = data.get("info") or {}
        ratios = data.get("ratios") or {}
        gic = data.get("gic") or {}
        out["ok"] = True
        out["company_name"] = info.get("name")
        out["description"] = (info.get("description") or "")[:320]
        out["sector"] = gic.get("sector") or info.get("sector")
        out["industry"] = gic.get("industry") or gic.get("subindustry")
        out["close_price"] = _num(ratios.get("lastPrice"))
        out["pe_ratio"] = _num(ratios.get("ttmPe") or ratios.get("pe") or ratios.get("apef"))
        out["roe"] = _num(ratios.get("roe"))
        # Tickertape ROE is often a fraction (0.015) or percent (1.5) — normalize
        if out["roe"] is not None and abs(out["roe"]) < 1.0:
            out["roe"] = round(out["roe"] * 100.0, 2)
        out["book_value"] = _num(ratios.get("bps"))
        out["roic"] = None  # not provided
        out["peg_ratio"] = None
        out["yoy_profit_growth"] = None
        out["net_debt_ebitda"] = None
        out["interest_coverage"] = None
        return out
    except Exception as exc:
        logger.warning("tickertape parse failed %s: %s", symbol, exc)
        return out


# Known Moneycontrol sc_id shortcuts (autosuggest is flaky under load)
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


def _moneycontrol_sc_id(ticker: str) -> Optional[str]:
    symbol = ticker.strip().upper()
    if symbol in MC_SC_ID_MAP:
        return MC_SC_ID_MAP[symbol]
    url = (
        "https://www.moneycontrol.com/mccode/common/autosuggestion_solr.php"
        f"?query={symbol}&type=1&format=json&callback=suggest"
    )
    resp = _session_get(url, "https://www.moneycontrol.com/")
    if resp is None or resp.status_code >= 400:
        return None
    text = resp.text
    # suggest([{...}])
    m = re.search(r"suggest\((\[.*\])\)\s*$", text, re.S)
    raw = m.group(1) if m else text
    try:
        arr = json.loads(raw)
    except Exception:
        return None
    for item in arr:
        if str(item.get("stock_name", "")).upper() == symbol or str(item.get("name", "")).upper() == symbol:
            return str(item.get("sc_id") or "")
    if arr:
        return str(arr[0].get("sc_id") or "") or None
    return None


def fetch_yahoo_quote(ticker: str) -> Dict[str, Any]:
    """
    Fourth free source (Yahoo Finance) so Refresh can still reach 3 websites
    when Tickertape rate-limits. Uses chart + optional yfinance; chart alone
    counts as a live site confirmation (price).
    """
    symbol = ticker.strip().upper()
    out: Dict[str, Any] = {"source": "yahoo", "ok": False}
    ysym = f"{symbol}.NS"

    # 1) Chart API (works on corp SSL with curl_cffi verify=0; quoteSummary often 401)
    for host in (
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
    ):
        resp = _session_get(
            f"{host}/v8/finance/chart/{ysym}?range=5d&interval=1d",
            "https://finance.yahoo.com/",
        )
        if resp is None or resp.status_code >= 400:
            continue
        try:
            payload = resp.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            meta = result.get("meta") or {}
            px = _num(meta.get("regularMarketPrice") or meta.get("previousClose"))
            if px is None:
                quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                closes = quote.get("close") or []
                closes = [c for c in closes if c is not None]
                if closes:
                    px = float(closes[-1])
            if px is not None:
                out["ok"] = True
                out["company_name"] = meta.get("longName") or meta.get("shortName") or symbol
                out["close_price"] = px
                out["pe_ratio"] = _num(meta.get("trailingPE"))
                out["roe"] = None
                out["roic"] = None
                out["peg_ratio"] = None
                out["yoy_profit_growth"] = None
                out["net_debt_ebitda"] = None
                out["interest_coverage"] = None
                out["sector"] = None
                out["industry"] = None
                break
        except Exception as exc:
            logger.warning("yahoo chart parse failed %s: %s", symbol, exc)

    # 2) Enrich PE via yfinance when SSL allows
    if out.get("ok") and out.get("pe_ratio") is None:
        try:
            import yfinance as yf

            info = yf.Ticker(ysym).info or {}
            pe = _num(info.get("trailingPE") or info.get("forwardPE"))
            if pe is not None:
                out["pe_ratio"] = pe
            if out.get("roe") is None:
                roe = _num(info.get("returnOnEquity"))
                if roe is not None:
                    out["roe"] = round(roe * 100.0, 2) if abs(roe) <= 1.5 else roe
            if not out.get("company_name") or out["company_name"] == symbol:
                out["company_name"] = info.get("longName") or info.get("shortName") or symbol
        except Exception as exc:
            logger.debug("yfinance enrich skipped %s: %s", symbol, exc)

    return out


def fetch_moneycontrol(ticker: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"source": "moneycontrol", "ok": False}
    sc_id = _moneycontrol_sc_id(ticker)
    if not sc_id:
        return out
    resp = _session_get(
        f"https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/{sc_id}",
        "https://www.moneycontrol.com/",
    )
    if resp is None or resp.status_code >= 400:
        return out
    try:
        payload = resp.json()
        data = payload.get("data") or {}
        out["ok"] = True
        out["sc_id"] = sc_id
        out["close_price"] = _num(data.get("pricecurrent"))
        # Prefer consensus PE when present (closer to Screener/Tickertape)
        out["pe_ratio"] = _num(data.get("PECONS")) or _num(data.get("PE"))
        out["sector"] = data.get("newSubsector") or data.get("SC_SUBSEC")
        out["roe"] = None
        out["roic"] = None
        out["peg_ratio"] = None
        out["yoy_profit_growth"] = None
        out["net_debt_ebitda"] = None
        out["interest_coverage"] = None
        return out
    except Exception as exc:
        logger.warning("moneycontrol parse failed %s: %s", ticker, exc)
        return out


def consensus_metric(
    metric: str,
    sources: List[Dict[str, Any]],
    *,
    allow_single: bool = False,
) -> Tuple[Optional[float], str, List[Dict[str, Any]]]:
    """
    Returns (value, status, detail_rows)
    status: verified | single_source | disputed | missing
    """
    readings = []
    for src in sources:
        if not src.get("ok"):
            continue
        val = _num(src.get(metric))
        if val is None:
            continue
        readings.append({"source": src.get("source"), "value": val})

    if not readings:
        return None, "missing", []
    if len(readings) == 1:
        status = "single_source" if allow_single else "unverified_single"
        return readings[0]["value"], status, readings

    # Pairwise agreement clusters
    values = [r["value"] for r in readings]
    # If all pairwise agree with median
    med = _median(values)
    agreeing = [v for v in values if _agree(v, med)]
    if len(agreeing) >= 2:
        return _median(agreeing), "verified", readings

    # Check if any pair agrees
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if _agree(values[i], values[j]):
                return _median([values[i], values[j]]), "verified", readings

    return None, "disputed", readings


def _fetch_bse_safe(symbol: str) -> Dict[str, Any]:
    try:
        import free_extra_sources as extra

        return extra.fetch_bse(symbol)
    except Exception as exc:
        logger.debug("bse source failed %s: %s", symbol, exc)
        return {"source": "bse", "ok": False}


def _fetch_nse_filings_safe(symbol: str) -> Dict[str, Any]:
    try:
        import free_extra_sources as extra

        return extra.fetch_nse_filings(symbol)
    except Exception as exc:
        logger.debug("nse filings source failed %s: %s", symbol, exc)
        return {"source": "nse_filings", "ok": False}


def fetch_verified_fundamentals(ticker: str) -> Dict[str, Any]:
    """
    Pull free sites (Screener + Tickertape + Moneycontrol + Yahoo + BSE + NSE filings),
    require ≥3 successful sources, consensus-check key metrics.
    NEVER invent ROCE/PEG/debt defaults.
    """
    from concurrent.futures import ThreadPoolExecutor

    symbol = ticker.strip().upper()
    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_s = pool.submit(fetch_screener, symbol)
        fut_t = pool.submit(fetch_tickertape, symbol)
        fut_m = pool.submit(fetch_moneycontrol, symbol)
        fut_y = pool.submit(fetch_yahoo_quote, symbol)
        fut_b = pool.submit(_fetch_bse_safe, symbol)
        fut_n = pool.submit(_fetch_nse_filings_safe, symbol)
        screener = fut_s.result()
        tickertape = fut_t.result()
        moneycontrol = fut_m.result()
        yahoo = fut_y.result()
        bse = fut_b.result()
        nse_filings = fut_n.result()
    # Screener/Tickertape/MC/Yahoo carry ratios; BSE adds exchange-filed PE/ROE/EPS,
    # NSE filings add the promoter block straight from SAST disclosures.
    sources = [screener, tickertape, moneycontrol, yahoo, bse, nse_filings]

    report: Dict[str, Any] = {
        "ticker": symbol,
        "sources_ok": [s["source"] for s in sources if s.get("ok")],
        "metrics": {},
        "raw_sources": {
            s["source"]: {k: v for k, v in s.items() if k not in {"description"}}
            for s in sources
        },
    }

    # Price — prefer multi-way consensus
    px, px_status, px_detail = consensus_metric("close_price", sources, allow_single=True)
    report["metrics"]["close_price"] = {"value": px, "status": px_status, "readings": px_detail}

    pe, pe_status, pe_detail = consensus_metric("pe_ratio", sources, allow_single=False)
    report["metrics"]["pe_ratio"] = {"value": pe, "status": pe_status, "readings": pe_detail}

    roe, roe_status, roe_detail = consensus_metric("roe", sources, allow_single=False)
    report["metrics"]["roe"] = {"value": roe, "status": roe_status, "readings": roe_detail}

    # ROCE usually Screener-only — accept single but flag; never default
    roce, roce_status, roce_detail = consensus_metric("roic", sources, allow_single=True)
    report["metrics"]["roic"] = {"value": roce, "status": roce_status, "readings": roce_detail}

    # BSE publishes the exchange's own filed ratios. When the scraped sites stay
    # silent or disagree with each other, the filed figure is the better record.
    trusted = {"verified", "derived_verified", "exchange_filed"}
    bse_pe = _num(bse.get("pe_ratio")) if bse.get("ok") else None
    bse_roe = _num(bse.get("roe")) if bse.get("ok") else None
    if bse_pe is not None and pe_status not in trusted:
        pe, pe_status = bse_pe, "exchange_filed"
        report["metrics"]["pe_ratio"] = {
            "value": pe,
            "status": pe_status,
            "readings": pe_detail or [{"source": "bse", "value": bse_pe}],
        }
    if bse_roe is not None and roe_status not in trusted:
        roe, roe_status = bse_roe, "exchange_filed"
        report["metrics"]["roe"] = {
            "value": roe,
            "status": roe_status,
            "readings": roe_detail or [{"source": "bse", "value": bse_roe}],
        }

    growth = _num(screener.get("yoy_profit_growth")) if screener.get("ok") else None
    report["metrics"]["yoy_profit_growth"] = {
        "value": growth,
        "status": "single_source" if growth is not None else "missing",
        "readings": [{"source": "screener", "value": growth}] if growth is not None else [],
    }

    peg = None
    peg_status = "missing"
    if pe is not None and pe_status in trusted and growth is not None and growth > 0:
        peg = round(pe / growth, 2)
        peg_status = "derived_verified"
    elif pe is not None and growth is not None and growth <= 0:
        peg = None
        peg_status = "invalid_negative_growth"
    else:
        # Yahoo sometimes has PEG directly
        ypeg = _num(yahoo.get("peg_ratio")) if yahoo.get("ok") else None
        if ypeg is not None and ypeg > 0:
            peg = ypeg
            peg_status = "single_source"
    report["metrics"]["peg_ratio"] = {"value": peg, "status": peg_status, "readings": []}

    # Debt / interest — not safely available free; leave missing (no fiction)
    for key in ("net_debt_ebitda", "interest_coverage"):
        report["metrics"][key] = {"value": None, "status": "missing", "readings": []}

    # Promoter block — NSE SAST disclosure is the primary record, Screener is the fallback
    nse_pledge = _num(nse_filings.get("promoter_pledge_pct")) if nse_filings.get("ok") else None
    holding = (
        _num(nse_filings.get("promoter_holding_pct"))
        if nse_filings.get("ok")
        else None
    )
    if holding is None and screener.get("ok"):
        holding = _num(screener.get("promoter_holding_pct"))
    if nse_pledge is not None:
        pledge_value, pledge_status = nse_pledge, "single_source"
        pledge_readings = [{"source": "nse_filings", "value": nse_pledge}]
    else:
        scr_pledge = screener.get("promoter_pledge_pct") if screener.get("ok") else None
        pledge_value = scr_pledge if scr_pledge is not None else (0.0 if holding is not None else None)
        pledge_status = "single_source" if holding is not None else "missing"
        pledge_readings = [{"source": "screener", "value": holding}]
    report["metrics"]["promoter_pledge_pct"] = {
        "value": pledge_value,
        "status": pledge_status,
        "readings": pledge_readings,
    }

    # Meta
    company = (
        screener.get("company_name")
        or tickertape.get("company_name")
        or bse.get("company_name")
        or nse_filings.get("company_name")
        or yahoo.get("company_name")
        or symbol
    )
    sector = (
        screener.get("sector")
        or tickertape.get("sector")
        or bse.get("sector")
        or moneycontrol.get("sector")
        or "—"
    )
    industry = (
        screener.get("industry")
        or tickertape.get("industry")
        or bse.get("industry")
        or "—"
    )
    description = (
        screener.get("description")
        or tickertape.get("description")
        or f"NSE equity {company}."
    )

    verified_core = pe_status in trusted and (
        roe_status in trusted or roce_status in {"single_source", "verified"}
    )
    sources_count = len(report["sources_ok"])
    # User rule: only accept when ≥3 free sites responded
    three_ok = sources_count >= 3
    is_verified = bool(verified_core and three_ok)

    # Flatten for app row — ONLY consensus / explicit values (None if not trusted)
    def take(metric: str, require_verified: bool = False) -> Optional[float]:
        meta = report["metrics"].get(metric) or {}
        status = meta.get("status")
        val = meta.get("value")
        if val is None:
            return None
        if require_verified and status not in trusted:
            return None
        if status in {"disputed", "missing", "unverified_single", "invalid_negative_growth"}:
            return None
        return float(val)

    flat = {
        "company_name": company,
        "description": description,
        "sector": sector,
        "industry": industry,
        "close_price": take("close_price"),
        "pe_ratio": take("pe_ratio", require_verified=True),
        "roe": take("roe", require_verified=True),
        "roic": take("roic"),
        "peg_ratio": take("peg_ratio"),
        "yoy_profit_growth": take("yoy_profit_growth"),
        "net_debt_ebitda": None,
        "interest_coverage": None,
        "promoter_pledge_pct": take("promoter_pledge_pct"),
        "promoter_holding_pct": holding,
        "fundamentals_verified": is_verified,
        "fundamentals_sources": report["sources_ok"],
        "sources_ok_count": sources_count,
        "fundamentals_report": report,
        "data_quality": (
            "VERIFIED"
            if is_verified
            else ("PARTIAL" if sources_count >= 1 else "UNVERIFIED")
        ),
    }
    return flat


def format_source_comparison(report: Dict[str, Any]) -> List[List[str]]:
    """Rows for UI table: Metric | Screener | Tickertape | Moneycontrol | Consensus | Status"""
    raw = report.get("raw_sources") or {}
    metrics = report.get("metrics") or {}
    keys = [
        ("close_price", "CMP"),
        ("pe_ratio", "P/E"),
        ("roe", "ROE %"),
        ("roic", "ROCE %"),
        ("yoy_profit_growth", "Profit growth %"),
        ("peg_ratio", "PEG"),
    ]
    rows = []
    for key, label in keys:
        scr = raw.get("screener", {}).get(key)
        tt = raw.get("tickertape", {}).get(key)
        mc = raw.get("moneycontrol", {}).get(key)
        meta = metrics.get(key) or {}
        cons = meta.get("value")
        status = str(meta.get("status", "missing")).upper()
        rows.append([
            label,
            "—" if scr is None else f"{scr:.2f}" if isinstance(scr, (int, float)) else str(scr),
            "—" if tt is None else f"{tt:.2f}" if isinstance(tt, (int, float)) else str(tt),
            "—" if mc is None else f"{mc:.2f}" if isinstance(mc, (int, float)) else str(mc),
            "—" if cons is None else f"{float(cons):.2f}",
            status,
        ])
    return rows
