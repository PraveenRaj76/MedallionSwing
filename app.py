"""
Medallion Swing — Forward-Test Validation App Controller
Fixed Quantity = 1 · Top navbar · Borderless HTML tables · No capital ledger
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Any, Dict, List, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

import data_pipeline as pipeline
import database_engine as db
import factor_engine as factors
import nse_data_provider as nse
import prod_runtime
import refresh_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(BASE_DIR, "templates", "fintech_flat.css")
ELEMENTS_PATH = os.path.join(BASE_DIR, "templates", "elements.html")

PAGE_SCREENER = "Screener"
PAGE_SEARCH = "Search Profile"
PAGE_VALIDATION = "Forward-Test"


@st.cache_data(show_spinner=False)
def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_css() -> str:
    try:
        return _load_text(CSS_PATH)
    except Exception as exc:
        logger.error("CSS load failed: %s", exc)
        return ""


def extract_html_block(marker: str) -> str:
    try:
        raw = _load_text(ELEMENTS_PATH)
    except Exception as exc:
        logger.error("elements.html load failed: %s", exc)
        return ""
    pattern = rf"<!--\s*{marker}_START\s*-->(.*?)<!--\s*{marker}_END\s*-->"
    match = re.search(pattern, raw, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def render_html(marker: str, **kwargs: Any) -> None:
    block = extract_html_block(marker)
    if not block:
        return
    try:
        st.markdown(block.format(**kwargs), unsafe_allow_html=True)
    except Exception as exc:
        logger.error("Template render failed for %s: %s", marker, exc)


def inject_theme() -> None:
    css = load_css()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_borderless_table(headers: List[str], rows: List[List[Any]], height: int = 320) -> None:
    ths = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            text = html.escape(str(cell))
            classes = []
            if i == 0:
                classes.append("ticker")
            if i > 0:
                classes.append("num")
            upper = str(cell).upper()
            if "SUCCESSFUL" in upper:
                classes.append("pos")
            elif "BAD TRADE" in upper or (isinstance(cell, str) and cell.startswith("-")):
                classes.append("neg")
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            if i == 0:
                cells.append(f"<td{class_attr}><strong>{text}</strong></td>")
            else:
                cells.append(f"<td{class_attr}>{text}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    table_html = f"""
    <table class="ms-table">
      <thead><tr>{ths}</tr></thead>
      <tbody>{''.join(body_rows) if body_rows else f'<tr><td colspan="{len(headers)}">No records</td></tr>'}</tbody>
    </table>
    <style>
      body {{ margin:0; background:transparent; font-family:'Plus Jakarta Sans',Inter,sans-serif; }}
      .ms-table {{ width:100%; border-collapse:collapse; font-size:0.9rem; }}
      .ms-table th {{ text-align:left; font-size:0.66rem; font-weight:650; letter-spacing:0.05em;
        text-transform:uppercase; color:#94a3b8; padding:0.55rem 0.6rem; border-bottom:1px solid #e2e8f0; }}
      .ms-table td {{ padding:0.65rem 0.6rem; border-bottom:1px solid #f1f5f9; color:#0f172a; }}
      .ms-table tr:hover td {{ background:#f8fafc; }}
      .ticker {{ font-weight:800; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
      .pos {{ color:#059669; font-weight:700; }} .neg {{ color:#dc2626; font-weight:700; }}
    </style>
    """
    components.html(table_html, height=height, scrolling=True)


def init_session_state() -> None:
    defaults = {
        "logged_in": False,
        "user_id": None,
        "username": None,
        "nav_page": PAGE_SCREENER,
        "sync_result": None,
        "order_flash": None,
        "selected_ticker": None,
        "best_stock_ranked": None,
        "best_stock_why": None,
        "best_stock_ticker": None,
        "best_stock_card": None,
        "fund_eta": None,
        "daily_refresh_running": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    legacy = {
        "Smart Screener": PAGE_SCREENER,
        "Virtual Portfolio Account": PAGE_VALIDATION,
        "Virtual Portfolio": PAGE_VALIDATION,
        "Paper Trading Terminal": PAGE_VALIDATION,
        "Forward-Test Validation": PAGE_VALIDATION,
    }
    current = st.session_state.get("nav_page")
    if current in legacy:
        st.session_state.nav_page = legacy[current]
    elif current not in (PAGE_SCREENER, PAGE_SEARCH, PAGE_VALIDATION):
        st.session_state.nav_page = PAGE_SCREENER


def logout_user() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    init_session_state()


def run_signal_sync(user_id: int, force: bool = False, fast: bool = False) -> None:
    st.session_state.sync_result = pipeline.sync_user_and_screener_data(
        user_id, force=force, fast=fast
    )


def run_light_validate(user_id: int) -> None:
    """Nav path: mark positions only — never block on full Yahoo universe pull."""
    clearances = pipeline.validate_active_signals(user_id)
    prev = st.session_state.get("sync_result") or {}
    st.session_state.sync_result = {
        **prev,
        "clearances": clearances,
        "skipped_heavy_sync": True,
        "message": prev.get("message")
        or f"Validated active signals ({len(clearances)} clearance(s)).",
    }


def execute_algorithmic_buy(
    user_id: int,
    ticker: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    source_page: str,
) -> Tuple[bool, str]:
    """Always opens exactly 1 share — no capital / risk sizing."""
    ok, message = db.open_signal(
        user_id=user_id,
        ticker=ticker,
        entry_price=float(entry_price),
        stop_loss=float(stop_loss),
        target=float(target),
    )
    if ok:
        try:
            st.session_state.order_flash = (
                f"Forward-test signal opened: 1 × {ticker.upper()} @ ₹{entry_price:,.2f} "
                f"from {source_page}."
            )
        except Exception:
            pass
    return ok, message


def _pause_screener_background_load() -> None:
    """Explicit Pause only — does not run when navigating to other pages."""
    st.session_state.daily_refresh_running = False
    refresh_worker.stop_worker(pause=True)


def _start_screener_background_load(user_id: int) -> None:
    """Start continuous background load until swing universe is ready (survives page changes)."""
    st.session_state.daily_refresh_running = True
    refresh_worker.start_worker(user_id=user_id, batch_size=12)


def render_top_navbar(nav: str, username: str) -> None:
    active = "ms-navbar__link--active"
    render_html(
        "NAVBAR",
        screener_active=active if nav == PAGE_SCREENER else "",
        search_active=active if nav == PAGE_SEARCH else "",
        validation_active=active if nav == PAGE_VALIDATION else "",
        username=html.escape(username or "user"),
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Screener", use_container_width=True, key="nav_screener"):
            st.session_state.nav_page = PAGE_SCREENER
            run_light_validate(int(st.session_state.user_id))
            st.rerun()
    with c2:
        if st.button("Search Profile", use_container_width=True, key="nav_search"):
            st.session_state.nav_page = PAGE_SEARCH
            run_light_validate(int(st.session_state.user_id))
            st.rerun()
    with c3:
        if st.button("Forward-Test", use_container_width=True, key="nav_val"):
            st.session_state.nav_page = PAGE_VALIDATION
            run_light_validate(int(st.session_state.user_id))
            st.rerun()
    with c4:
        if st.button("Log Out", use_container_width=True, key="nav_logout"):
            _pause_screener_background_load()
            logout_user()
            st.rerun()


def render_login_gate() -> None:
    render_html("BANNER")
    mode = st.radio(
        "Authentication",
        options=["Sign In", "Create Account"],
        horizontal=True,
        key="auth_mode_radio",
        label_visibility="collapsed",
    )
    title = "Welcome Back" if mode == "Sign In" else "Create Forward-Test Account"
    render_html("AUTH_HEADER", auth_title=title)

    with st.form("auth_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm Password", type="password") if mode == "Create Account" else None
        submitted = st.form_submit_button(
            "Sign In" if mode == "Sign In" else "Create Account",
            use_container_width=True,
        )

    if not submitted:
        return

    if mode == "Create Account":
        if password != confirm:
            st.error("Passwords do not match.")
            return
        ok, message, user_id = db.register_user(username, password)
        if not ok:
            st.error(message)
            return
        st.session_state.logged_in = True
        st.session_state.user_id = user_id
        st.session_state.username = username.strip()
        st.session_state.sync_result = None  # trigger fast bootstrap after rerun
        st.success(message)
        st.rerun()

    ok, message, user_id = db.verify_user(username, password)
    if not ok:
        st.error(message)
        return
    st.session_state.logged_in = True
    st.session_state.user_id = user_id
    st.session_state.username = username.strip()
    st.session_state.sync_result = None  # trigger fast bootstrap after rerun
    st.success(message)
    st.rerun()


def _chart_layout(fig: go.Figure, height: int = 320) -> go.Figure:
    fig.update_layout(
        height=height,
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        font=dict(family="Plus Jakarta Sans, sans-serif", color="#0f172a"),
        margin=dict(l=40, r=20, t=40, b=30),
        legend=dict(orientation="h", y=1.12),
    )
    return fig


def create_price_sma_chart(df_price: pd.DataFrame, ticker: str) -> go.Figure:
    close_prices = df_price["close"].to_numpy(dtype=float)
    dates_full = df_price["date"]
    sma_200_values = pipeline.compute_sma(close_prices, 200)
    dates_sma_200 = dates_full.iloc[199:] if len(close_prices) >= 200 else dates_full[:0]
    sma_50_values = pipeline.compute_sma(close_prices, 50)
    dates_sma_50 = dates_full.iloc[49:] if len(close_prices) >= 50 else dates_full[:0]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=dates_full, y=close_prices, name="Close", line=dict(color="#2563eb", width=2))
    )
    if len(sma_50_values):
        fig.add_trace(
            go.Scatter(
                x=dates_sma_50, y=sma_50_values, name="50 SMA",
                line=dict(color="#f59e0b", width=1.5, dash="dot"),
            )
        )
    if len(sma_200_values):
        fig.add_trace(
            go.Scatter(
                x=dates_sma_200, y=sma_200_values, name="200 SMA",
                line=dict(color="#dc2626", width=2, dash="dash"),
            )
        )
    fig.update_layout(title=f"{ticker} — Price & Moving Averages")
    return _chart_layout(fig, 360)


def create_volume_chart(df_price: pd.DataFrame, ticker: str) -> go.Figure:
    dates_full = df_price["date"]
    colors = [
        "#059669" if df_price["close"].iloc[i] >= df_price["open"].iloc[i] else "#dc2626"
        for i in range(len(df_price))
    ]
    fig = go.Figure(
        go.Bar(x=dates_full, y=df_price["volume"], marker=dict(color=colors), name="Volume")
    )
    fig.update_layout(title=f"{ticker} — Volume")
    return _chart_layout(fig, 260)


def create_rsi_chart(df_price: pd.DataFrame, ticker: str) -> go.Figure:
    close_prices = df_price["close"].to_numpy(dtype=float)
    dates_full = df_price["date"]
    rsi_idx, rsi_values = pipeline.compute_rsi_series(close_prices, 14)
    dates_rsi = dates_full.iloc[rsi_idx] if len(rsi_idx) else dates_full[:0]
    fig = go.Figure()
    if len(rsi_values):
        fig.add_trace(
            go.Scatter(x=dates_rsi, y=rsi_values, name="RSI(14)", line=dict(color="#64748b", width=2))
        )
    fig.add_hline(y=65, line_dash="dash", line_color="#dc2626", annotation_text="65 lock")
    fig.add_hline(y=45, line_dash="dot", line_color="#94a3b8")
    fig.update_layout(title=f"{ticker} — RSI (14)", yaxis=dict(range=[0, 100]))
    return _chart_layout(fig, 280)


def create_technical_chart(df_price: pd.DataFrame, ticker: str) -> go.Figure:
    """Legacy combined chart (kept for E2E / smoke). Prefer panel charts in Search Profile."""
    close_prices = df_price["close"].to_numpy(dtype=float)
    dates_full = df_price["date"]
    sma_200_values = pipeline.compute_sma(close_prices, 200)
    dates_sma_200 = dates_full.iloc[199:] if len(close_prices) >= 200 else dates_full[:0]
    rsi_idx, rsi_values = pipeline.compute_rsi_series(close_prices, 14)
    dates_rsi = dates_full.iloc[rsi_idx] if len(rsi_idx) else dates_full[:0]
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=("Price & 200-Day SMA", "Volume", "RSI (14)"),
    )
    fig.add_trace(
        go.Scatter(x=dates_full, y=close_prices, name="Close", line=dict(color="#2563eb", width=2)),
        row=1, col=1,
    )
    if len(sma_200_values):
        fig.add_trace(
            go.Scatter(
                x=dates_sma_200, y=sma_200_values, name="200 SMA",
                line=dict(color="#dc2626", width=2, dash="dash"),
            ),
            row=1, col=1,
        )
    colors = [
        "#059669" if df_price["close"].iloc[i] >= df_price["open"].iloc[i] else "#dc2626"
        for i in range(len(df_price))
    ]
    fig.add_trace(
        go.Bar(x=dates_full, y=df_price["volume"], marker=dict(color=colors), showlegend=False),
        row=2, col=1,
    )
    if len(rsi_values):
        fig.add_trace(
            go.Scatter(x=dates_rsi, y=rsi_values, name="RSI", line=dict(color="#64748b", width=2)),
            row=3, col=1,
        )
        fig.add_hline(y=65, line_dash="dash", line_color="#dc2626", row=3, col=1)
    fig.update_layout(
        height=760, template="plotly_white", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc", font=dict(family="Plus Jakarta Sans, sans-serif", color="#0f172a"),
        margin=dict(l=40, r=20, t=50, b=30), legend=dict(orientation="h", y=1.08),
    )
    return fig


def _render_checklist_block(title: str, block: dict) -> None:
    st.markdown(f'<div class="ms-section"><h3 class="ms-title">{title}</h3></div>', unsafe_allow_html=True)
    rows = []
    for item in block["items"]:
        mark = "PASS" if item["passed"] else "FAIL"
        rows.append([
            item["name"],
            item["value"],
            f"{item['marks']:.1f}/{item['max_marks']:.0f}",
            mark,
            item["note"],
        ])
    render_borderless_table(
        ["Filter", "Value", "Marks", "Status", "Why"],
        rows,
        height=min(80 + 36 * len(rows), 420),
    )
    st.caption(
        f"**Subtotal:** {block['total_marks']:.1f} / {block['max_marks']:.0f} "
        f"· Cleared {block['cleared']}/{block['total_filters']} filters "
        f"· {block['pct']:.1f}%"
    )


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce None/NaN/blank to default — pandas .get(key, 0) fails when key exists as None."""
    try:
        if val is None:
            return float(default)
        if isinstance(val, float) and val != val:  # NaN
            return float(default)
        if val == "" or val == "—":
            return float(default)
        return float(val)
    except (TypeError, ValueError):
        return float(default)


def _fmt_num(val: Any, fmt: str = ".1f", blank: str = "—") -> str:
    if val is None or val == "" or (isinstance(val, float) and val != val):
        return blank
    try:
        return format(_safe_float(val), fmt)
    except Exception:
        return blank


def _fmt_score(val: Any) -> str:
    """Show — when fundamental score missing / zero from price-only sync."""
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    try:
        n = float(val)
        if n <= 0:
            return "—"
        return f"{n:.0f}"
    except (TypeError, ValueError):
        return "—"


def _render_buy_panel(user_id: int, row: pd.Series, source_page: str, prefix: str) -> None:
    ticker = str(row["ticker"]).upper()
    close_price = _safe_float(row.get("close_price"), 0.0)
    atr = _safe_float(row.get("atr_value"), 0.0)
    levels = pipeline.build_trade_levels(close_price, atr)
    render_html(
        "EXECUTION_TICKET",
        ticker=ticker,
        cmp=f"{close_price:,.2f}",
        atr=f"{atr:,.2f}",
        stop_loss=f"{levels['stop_loss']:,.2f}",
        target=f"{levels['target']:,.2f}",
        rrr=f"{levels['rrr']:.2f}",
    )
    if st.button("EXECUTE ALGORITHMIC BUY", use_container_width=True, key=f"{prefix}_buy"):
        ok, message = execute_algorithmic_buy(
            user_id=user_id,
            ticker=ticker,
            entry_price=close_price,
            stop_loss=levels["stop_loss"],
            target=levels["target"],
            source_page=source_page,
        )
        if ok:
            st.success(message)
            st.rerun()
        else:
            st.error(message)


def _price_kind_label(kind: Any) -> str:
    k = str(kind or "LIVE").strip().upper()
    return {"LIVE": "Live", "LAST": "Last", "PREV_CLOSE": "Prev close"}.get(k, k or "Live")


def _fmt_cmp_cell(row: Any) -> str:
    px = _safe_float(row.get("close_price"), 0.0)
    label = _price_kind_label(row.get("price_kind"))
    return f"₹{px:,.2f} ({label})"


def _update_fund_eta(fresult: Dict[str, Any], fcov: Dict[str, Any]) -> Dict[str, Any]:
    """Track cumulative fill rate for a stable ETA across auto-continue batches."""
    now = time.time()
    meta = st.session_state.get("fund_eta") or {}
    filled = int(fresult.get("filled") or 0)
    if not meta.get("t0") or meta.get("verified0") is None:
        meta = {
            "t0": now - float(fresult.get("elapsed_sec") or 0),
            "verified0": max(0, int(fcov.get("verified") or 0) - filled),
        }
    elapsed = max(now - float(meta["t0"]), 0.001)
    verified = int((fresult.get("coverage") or fcov).get("verified") or fcov.get("verified") or 0)
    gained = max(0, verified - int(meta["verified0"]))
    rate_per_min = gained / elapsed * 60.0 if gained > 0 else float(fresult.get("batch_rate_per_min") or 0)
    missing = int((fresult.get("coverage") or fcov).get("missing") or fcov.get("missing") or 0)
    eta_min = (missing / rate_per_min) if rate_per_min > 0 and missing > 0 else None
    meta.update(
        {
            "rate_per_min": round(rate_per_min, 2),
            "eta_minutes": round(eta_min, 1) if eta_min is not None else None,
            "last_message": fresult.get("message"),
            "verified": verified,
            "missing": missing,
            "total": int((fresult.get("coverage") or fcov).get("total") or fcov.get("total") or 0),
        }
    )
    if missing <= 0:
        meta = {"t0": None, "verified0": None, "eta_minutes": 0, "rate_per_min": rate_per_min,
                "verified": verified, "missing": 0, "total": meta.get("total"),
                "last_message": fresult.get("message")}
    st.session_state.fund_eta = meta
    return meta


def _fmt_eta(eta_min: Any) -> str:
    if eta_min is None:
        return ""
    try:
        eta = float(eta_min)
    except (TypeError, ValueError):
        return ""
    if eta <= 0:
        return ""
    hrs = int(eta // 60)
    mins = int(round(eta % 60))
    if hrs:
        return f" · ETA ~{hrs}h {mins}m"
    return f" · ETA ~{mins} min"


def _render_daily_progress(status: Dict[str, Any]) -> None:
    target = max(int(status.get("target") or nse.UNIVERSE_TARGET_HINT), 1)
    complete = int(status.get("complete") or 0)
    missing = int(status.get("missing") or max(0, target - complete))
    failed = int(status.get("failed") or 0)
    eta_bit = _fmt_eta(status.get("eta_minutes"))

    st.markdown(f"##### {nse.universe_label()} data refresh")
    st.markdown(
        f"<div style='font-size:1.75rem;font-weight:800;margin:0.2rem 0 0.6rem 0;'>"
        f"Ready <span style='color:#2563eb'>{complete}</span> / {target}</div>",
        unsafe_allow_html=True,
    )
    st.progress(
        min(1.0, complete / target),
        text=f"Swing stocks fully loaded {complete} / {target}",
    )
    st.caption(
        "Counts **+1 only when** price + fundamentals + technicals + checklist fields are all present for a stock."
    )

    if not status.get("is_today"):
        st.warning(
            f"New day (**{status.get('today')}** IST) — click **Refresh**. "
            "Yesterday's table stays hidden."
        )
    elif complete >= target:
        st.success(f"Today (**{status.get('as_of')}**) — **{complete} / {target}** stocks ready.")
    elif status.get("load_finished") and failed > 0:
        st.warning(
            f"Finished with gaps — **{complete} / {target}** ready, **{failed}** could not load after retries "
            "(listed at the bottom)."
        )
    elif status.get("status") == "paused":
        st.info(
            f"Paused at **{complete} / {target}** ready · **{missing}** remaining. "
            "Click **Resume load** once — it keeps running in the background while you use Search / Forward-Test."
        )
    elif status.get("running") or status.get("status") == "running":
        st.info(
            f"Loading in background… **{complete} / {target}** ready · **{missing}** remaining"
            f"{eta_bit}. Navigate freely — load continues until complete."
        )
    elif missing > 0:
        st.warning(
            f"Incomplete: **{complete} / {target}**. Click **Refresh** to reset and reload."
        )
    msg = status.get("message") or ""
    if msg:
        st.caption(msg)


def _render_failed_loads(status: Dict[str, Any]) -> None:
    failures = status.get("failures") or []
    if not failures:
        return
    st.markdown(
        '<div class="ms-section"><h3 class="ms-title">Not loaded (after retries)</h3>'
        '<p class="ms-muted">Still missing core price / fund / tech fields after retries. '
        "Debt & interest coverage alone no longer block a name. "
        "Force re-refresh later to try again.</p></div>",
        unsafe_allow_html=True,
    )
    rows = [[f["ticker"], f.get("attempts", "—"), str(f.get("error") or "—")[:80]] for f in failures]
    render_borderless_table(["Ticker", "Attempts", "Last error"], rows, height=min(80 + 36 * len(rows), 280))


def render_screener(user_id: int) -> None:
    render_html("BANNER")
    if st.session_state.get("order_flash"):
        st.success(st.session_state.order_flash)

    status = pipeline.daily_load_status()
    target = int(status.get("target") or nse.UNIVERSE_TARGET_HINT)
    complete = int(status.get("complete") or 0)
    day_done = bool(status.get("is_today") and complete >= target)

    if day_done and status.get("status") not in {"complete", "failed"}:
        db.set_screener_refresh_state(
            status="complete",
            message=f"{nse.universe_label()} ready — {complete}/{target}.",
        )
        status = pipeline.daily_load_status()
        day_done = True

    st.markdown(
        '<div class="ms-section"><h2 class="ms-title">Smart Screener</h2>'
        f'<p class="ms-muted">One Refresh loads <b>{nse.universe_label()}</b> '
        "(~200 swing names). Progress counts a stock only when "
        "price, fundamentals, technicals and checklist fields are all ready. Qty = 1.</p></div>",
        unsafe_allow_html=True,
    )

    _render_daily_progress(status)

    pending_n = int(status.get("pending") or 0)
    worker_alive = refresh_worker.is_worker_alive()
    # If DB says running but worker died (restart), resume automatically
    if (
        not worker_alive
        and status.get("status") == "running"
        and pending_n > 0
        and status.get("is_today")
        and not day_done
    ):
        _start_screener_background_load(user_id)
        worker_alive = refresh_worker.is_worker_alive()
    if worker_alive:
        st.session_state.daily_refresh_running = True

    # Needs Resume only when work remains and no background worker is active
    is_paused = (
        not worker_alive
        and pending_n > 0
        and status.get("is_today")
        and not day_done
        and status.get("status") in {"running", "paused", "idle", "failed"}
    )

    if worker_alive and pending_n > 0 and status.get("is_today"):
        if st.button("Pause load", use_container_width=True, key="screener_pause_load"):
            _pause_screener_background_load()
            st.rerun()
    elif is_paused and pending_n > 0 and status.get("is_today"):
        r1, r2 = st.columns(2)
        with r1:
            if st.button("Resume load", type="primary", use_container_width=True, key="screener_resume_load"):
                db.set_screener_refresh_state(
                    status="running",
                    message=f"Resumed in background — {complete}/{target} ready…",
                )
                _start_screener_background_load(user_id)
                st.rerun()
        with r2:
            if st.button("Pause / stay idle", use_container_width=True, key="screener_stay_paused"):
                _pause_screener_background_load()
                st.rerun()

    if not day_done and not (status.get("load_finished") and complete < target):
        # Don't offer wipe-Refresh while a paused progressive load exists — use Resume or Force.
        show_refresh = (
            not worker_alive
            and not st.session_state.get("daily_refresh_running")
            and complete == 0
            and int(status.get("pending") or 0) == 0
        ) or (not status.get("is_today"))
        if show_refresh:
            if st.button("Refresh", type="primary", use_container_width=True, key="screener_daily_refresh"):
                with st.spinner(
                    f"Resetting to 0 / {target} and starting {nse.universe_label()} refresh…"
                ):
                    started = pipeline.begin_daily_refresh(user_id=user_id)
                    st.session_state.sync_result = started
                    st.session_state.fund_eta = None
                    _start_screener_background_load(user_id)
                st.rerun()
        elif (
            not worker_alive
            and not st.session_state.get("daily_refresh_running")
            and status.get("is_today")
            and complete == 0
            and db.leaderboard_count() == 0
        ):
            if st.button("Refresh", type="primary", use_container_width=True, key="screener_daily_refresh_empty"):
                with st.spinner(f"Starting {nse.universe_label()} refresh…"):
                    started = pipeline.begin_daily_refresh(user_id=user_id)
                    st.session_state.sync_result = started
                    _start_screener_background_load(user_id)
                st.rerun()
    elif day_done:
        st.caption("Refresh hidden — today's swing-universe load is complete.")
        with st.expander("Force re-refresh (wipe today and reload)", expanded=False):
            if st.button("Force re-refresh now", type="secondary", use_container_width=True, key="screener_force_refresh"):
                with st.spinner(f"Force reset — clearing to 0 / {target}…"):
                    started = pipeline.begin_daily_refresh(user_id=user_id)
                    st.session_state.sync_result = started
                    st.session_state.fund_eta = None
                    _start_screener_background_load(user_id)
                st.rerun()
    else:
        # Finished with some failures — allow force retry
        with st.expander("Force re-refresh (retry including failed names)", expanded=False):
            if st.button("Force re-refresh now", type="secondary", use_container_width=True, key="screener_force_refresh2"):
                with st.spinner(f"Force reset — clearing to 0 / {target}…"):
                    started = pipeline.begin_daily_refresh(user_id=user_id)
                    st.session_state.sync_result = started
                    _start_screener_background_load(user_id)
                st.rerun()

    status = pipeline.daily_load_status()
    worker_alive = refresh_worker.is_worker_alive()
    still_loading = bool(
        worker_alive
        and status.get("is_today")
        and int(status.get("pending") or 0) > 0
    )

    if worker_alive and int(status.get("pending") or 0) == 0:
        st.session_state.daily_refresh_running = False

    if still_loading:
        complete_now = int(status.get("complete") or 0)
        target_now = int(status.get("target") or nse.UNIVERSE_TARGET_HINT)
        st.caption(
            f"Background load active… {complete_now} / {target_now} ready"
            f"{_fmt_eta(status.get('eta_minutes'))}. Safe to use Search / Forward-Test."
        )

    status = pipeline.daily_load_status()

    df = pipeline.filter_display_ready(db.get_leaderboard(limit=1000))
    if df is None or df.empty:
        if status.get("running") or still_loading or refresh_worker.is_worker_alive():
            st.info(
                f"Table grows as each stock finishes. "
                f"Currently **{status.get('complete', 0)} / {status.get('target', nse.UNIVERSE_TARGET_HINT)}** ready."
            )
        elif not status.get("is_today"):
            st.info("No data for today yet. Click **Refresh** to begin.")
        else:
            st.warning("No fully ready stocks yet.")
        _render_failed_loads(status)
        if still_loading:
            time.sleep(0.45)
            st.rerun()
        return

    display = df
    rows = []
    for _, r in display.iterrows():
        rows.append([
            r["ticker"],
            r["company_name"],
            r["sector"] if pd.notna(r.get("sector")) and str(r.get("sector")).strip() not in {"", "—", "nan"} else "—",
            _fmt_score(r.get("fundamental_score")),
            _fmt_score(r.get("technical_score")),
            _fmt_score(r.get("composite_score")),
            _fmt_cmp_cell(r),
            str(r.get("last_updated") or "—")[-8:] if r.get("last_updated") else "—",
            "Yes" if int(_safe_float(r.get("is_buyable"), 0)) else "No",
        ])
    render_borderless_table(
        ["Ticker", "Company", "Sector", "Fund.", "Tech.", "Score", "CMP", "Updated", "Buyable"],
        rows,
        height=420,
    )
    st.caption(
        f"Showing **{len(rows)}** ready stocks "
        f"(**{status.get('complete')} / {status.get('target')}**). "
        "Each row has full price + fund + tech + checklist data."
    )

    tickers = display["ticker"].astype(str).tolist()
    default_ix = 0
    if st.session_state.selected_ticker in tickers:
        default_ix = tickers.index(st.session_state.selected_ticker)
    selected = st.selectbox("Select ticker", options=tickers, index=default_ix)
    st.session_state.selected_ticker = selected
    row = display[display["ticker"] == selected].iloc[0]

    close_price = _safe_float(row.get("close_price"), 0.0)
    levels = pipeline.build_trade_levels(close_price, _safe_float(row.get("atr_value"), 0.0))
    is_buyable, reason = pipeline.check_buyability(row)
    badge = extract_html_block("BADGE_BUY" if is_buyable else "BADGE_HOLD")
    render_html(
        "ASSET_HEADER",
        company_name=row.get("company_name", selected) or selected,
        ticker=selected,
        description=row.get("description", "") or "",
        sector=row.get("sector", "—") or "—",
        industry=row.get("industry", "—") or "—",
        decision_badge=badge,
    )
    if not is_buyable and "OVEREXTENDED" in reason:
        st.markdown(f'<div class="ms-warning">{reason}</div>', unsafe_allow_html=True)
    else:
        st.caption(reason)

    price_note = _price_kind_label(row.get("price_kind"))
    src = str(row.get("price_source") or "").strip()
    src_bit = f" · {src}" if src else ""
    prev = row.get("prev_close")
    prev_bit = (
        f" · prev close ₹{_safe_float(prev):,.2f}"
        if prev not in (None, "") and _safe_float(prev) > 0
        else ""
    )
    st.caption(f"CMP type: **{price_note}**{src_bit}{prev_bit}")

    # Sector pack + Quality / Value / Timing
    card = factors.full_factor_scorecard(row)
    pack = card.get("sector_pack", "general")
    qvt = card.get("qvt") or {}
    st.caption(
        f"Checklist pack: **{pack}** · "
        f"Quality {qvt.get('quality', 0):.0f} · Value {qvt.get('value', 0):.0f} · Timing {qvt.get('timing', 0):.0f}"
    )

    render_html(
        "TRADE_PARAMS",
        ticker=selected,
        cmp=f"{close_price:,.2f}",
        stop_loss=f"{levels['stop_loss']:,.2f}",
        target=f"{levels['target']:,.2f}",
        rrr=f"{levels['rrr']:.2f}",
    )
    sma200 = _safe_float(row.get("sma_200"), 0.0)
    sma_trend = "Above 200 SMA" if close_price > sma200 else "Below 200 SMA"
    render_html(
        "REPORT_CARD",
        ticker=selected,
        roic=_fmt_num(row.get("roic"), ".1f"),
        net_debt_ebitda=_fmt_num(row.get("net_debt_ebitda"), ".2f"),
        peg=_fmt_num(row.get("peg_ratio"), ".2f"),
        interest_coverage=_fmt_num(row.get("interest_coverage"), ".1f"),
        promoter_pledge=_fmt_num(row.get("promoter_pledge_pct"), ".1f"),
        profit_growth=_fmt_num(row.get("yoy_profit_growth"), ".1f"),
        sma_trend=sma_trend,
        rsi=_fmt_num(row.get("rsi_14"), ".1f"),
        delivery_pct=_fmt_num(row.get("delivery_pct_10d"), ".1f"),
        composite_score=_fmt_num(row.get("composite_score"), ".0f"),
    )
    if is_buyable:
        _render_buy_panel(user_id, row, PAGE_SCREENER, "screen")
    else:
        st.info("Signal entry locked until trend / RSI filters clear.")

    _render_failed_loads(status)
    _render_best_stock_lab(display)

    if still_loading:
        time.sleep(0.45)
        st.rerun()


def _render_best_stock_lab(display: pd.DataFrame) -> None:
    st.markdown(
        '<div class="ms-section"><h2 class="ms-title">Best Stock Lab</h2>'
        '<p class="ms-muted">Takes every name sharing the top 3 composite scores across Nifty, '
        "re-ranks with an expanded fundamental + technical checklist, then explains the winner.</p></div>",
        unsafe_allow_html=True,
    )
    if st.button("Find Best Stock in Top-Score Pool", type="primary", use_container_width=True, key="best_stock_run"):
        with st.spinner(
            "Live multi-source verify (Screener + Tickertape + Moneycontrol) "
            "then expanded checklist — may take a few minutes…"
        ):
            pool = factors.select_top_score_pool(display, top_n_scores=3)
            ranked, best_row, why = factors.rank_best_stocks(pool, live_verify=True, max_verify=12)
            st.session_state.best_stock_ranked = ranked
            st.session_state.best_stock_why = why
            st.session_state.best_stock_ticker = (
                str(best_row.get("ticker")) if best_row is not None else None
            )
            if best_row is not None:
                st.session_state.best_stock_card = factors.full_factor_scorecard(best_row)

    ranked = st.session_state.get("best_stock_ranked")
    if ranked is None or (isinstance(ranked, pd.DataFrame) and ranked.empty):
        st.caption("Click the button to analyse the current Nifty leaderboard.")
        return

    top_scores = sorted(ranked["db_composite"].unique(), reverse=True)[:3]
    st.info(
        f"Top score band(s): {', '.join(f'{s:.1f}' for s in top_scores)} · "
        f"Pool size: **{len(ranked)}** stocks · Winner: **{st.session_state.get('best_stock_ticker')}**"
    )
    st.markdown(st.session_state.get("best_stock_why", ""))

    compare_rows = []
    for _, r in ranked.head(25).iterrows():
        compare_rows.append([
            r["ticker"],
            r["company_name"][:28],
            r["sector"][:18],
            str(r.get("data_quality", "—")),
            f"{float(r['db_composite']):.1f}",
            f"{float(r['fund_marks']):.1f}",
            f"{float(r['tech_marks']):.1f}",
            f"{float(r['expanded_total']):.1f}",
            f"{int(r['fund_cleared'])}/{int(r['fund_filters'])}",
            f"{int(r['tech_cleared'])}/{int(r['tech_filters'])}",
            "Yes" if int(r["is_buyable"]) else "No",
        ])
    render_borderless_table(
        ["Ticker", "Company", "Sector", "Quality", "DB", "Fund", "Tech", "Expanded", "F-CLR", "T-CLR", "Buyable"],
        compare_rows,
        height=360,
    )

    card = st.session_state.get("best_stock_card")
    if card:
        st.markdown(
            f'<div class="ms-section"><h3 class="ms-title">Why {html.escape(str(st.session_state.get("best_stock_ticker")))} wins</h3>'
            f'<p class="ms-muted">Expanded total '
            f'{card["composite_marks"]:.1f}/{card["composite_max"]:.0f} '
            f'({card["composite_pct"]:.1f}%)</p></div>',
            unsafe_allow_html=True,
        )
        _render_checklist_block("Winner — Fundamental checklist", card["fundamental"])
        _render_checklist_block("Winner — Technical checklist", card["technical"])


def render_search(user_id: int) -> None:
    render_html("BANNER")
    if st.session_state.get("order_flash"):
        st.success(st.session_state.order_flash)
    st.markdown(
        '<div class="ms-section"><h2 class="ms-title">Search Profile</h2>'
        '<p class="ms-muted">Live NSE profile · fundamentals + technicals · charts with plain-English readouts · '
        "every search pulls latest quotes independently of Screener load.</p></div>",
        unsafe_allow_html=True,
    )
    try:
        sstatus = pipeline.daily_load_status()
        if sstatus.get("status") in {"paused", "running"} and int(sstatus.get("pending") or 0) > 0:
            st.caption(
                f"Screener load status **{sstatus.get('complete', 0)}/"
                f"{sstatus.get('target', nse.UNIVERSE_TARGET_HINT)}** — "
                "this Search fetch is separate and live."
            )
    except Exception:
        pass

    c_in, c_btn = st.columns([3, 1])
    with c_in:
        ticker_input = st.text_input("Ticker", placeholder="TCS, RELIANCE, INFY, HDFCBANK", key="search_ticker_input")
    with c_btn:
        st.write("")
        st.write("")
        force = st.button("Refresh latest", use_container_width=True, key="search_force_refresh")

    if not ticker_input:
        return
    ticker = ticker_input.strip().upper()

    # Always force live fetch on Search Profile (independent of Screener batch job)
    with st.spinner(f"Fetching latest live NSE data for {ticker}…"):
        row = pipeline.ensure_ticker_live(ticker, include_fundamentals=True, force_refresh=True)
        history = pipeline.generate_price_history(ticker, 0, 250)

    if row is None:
        st.error(
            f"Could not load live data for '{ticker}'. "
            f"Yahoo/Screener/Tickertape may be blocked on this network. "
            f"In PowerShell run: `$env:MEDALLION_SSL_VERIFY='0'` then restart Streamlit, "
            f"or run `python sync_nifty500_local.py --clear`."
        )
        return

    if force:
        st.success(f"Refreshed live quotes & fundamentals for {ticker}.")

    close_price = float(row["close_price"])
    if history is None or history.empty:
        history = pipeline.generate_price_history(ticker, close_price, 250)

    is_buyable, reason = pipeline.check_buyability(row)
    badge = extract_html_block("BADGE_BUY" if is_buyable else "BADGE_HOLD")
    render_html(
        "ASSET_HEADER",
        company_name=row.get("company_name", ticker),
        ticker=ticker,
        description=row.get("description", ""),
        sector=row.get("sector", "—"),
        industry=row.get("industry", "—"),
        decision_badge=badge,
    )
    if not is_buyable:
        if "OVEREXTENDED" in reason:
            st.markdown(f'<div class="ms-warning">{reason}</div>', unsafe_allow_html=True)
        else:
            st.warning(reason)
    else:
        st.success(reason)

    # Snapshot metrics (Screener-style)
    snap = factors.profile_snapshot(row)
    st.markdown(
        '<div class="ms-section"><h3 class="ms-title">Stock Snapshot (live)</h3></div>',
        unsafe_allow_html=True,
    )
    snap_rows = [[k, v] for k, v in snap.items()]
    # render in two columns of kv via table
    mid = (len(snap_rows) + 1) // 2
    left, right = st.columns(2)
    with left:
        render_borderless_table(["Metric", "Value"], snap_rows[:mid], height=320)
    with right:
        render_borderless_table(["Metric", "Value"], snap_rows[mid:], height=320)

    levels = pipeline.build_trade_levels(close_price, float(row.get("atr_value", 0) or 0))
    render_html(
        "TRADE_PARAMS",
        ticker=ticker,
        cmp=f"{close_price:,.2f}",
        stop_loss=f"{levels['stop_loss']:,.2f}",
        target=f"{levels['target']:,.2f}",
        rrr=f"{levels['rrr']:.2f}",
    )

    # Multi-source consensus table (Screener + Tickertape + Moneycontrol)
    report = row.get("fundamentals_report") if hasattr(row, "get") else None
    quality = str(row.get("data_quality") or "UNVERIFIED")
    sources = row.get("fundamentals_sources") or []
    st.markdown(
        f'<div class="ms-section"><h3 class="ms-title">Multi-source verification</h3>'
        f'<p class="ms-muted">Quality: <strong>{html.escape(quality)}</strong> · '
        f'Sources: {html.escape(", ".join(sources) if sources else "none")}. '
        f"Metrics enter scoring only after Screener + Tickertape + Moneycontrol consensus.</p></div>",
        unsafe_allow_html=True,
    )
    if report:
        import multi_source_data as msd

        cmp_rows = msd.format_source_comparison(report)
        render_borderless_table(
            ["Metric", "Screener", "Tickertape", "Moneycontrol", "Consensus", "Status"],
            cmp_rows,
            height=280,
        )
    elif quality == "UNVERIFIED":
        st.warning(
            "Fundamentals not verified yet. Click **Refresh latest** to pull Screener + "
            "Tickertape + Moneycontrol and block placeholder values."
        )

    card = factors.full_factor_scorecard(row, history)
    st.markdown(
        f'<div class="ms-section"><h3 class="ms-title">Filter Scorecard</h3>'
        f'<p class="ms-muted">Expanded checklist total '
        f'<strong>{card["composite_marks"]:.1f}/{card["composite_max"]:.0f}</strong> '
        f'({card["composite_pct"]:.1f}%) · data quality {html.escape(quality)}</p></div>',
        unsafe_allow_html=True,
    )
    _render_checklist_block("Fundamental filters (value → marks)", card["fundamental"])
    _render_checklist_block("Technical filters (value → marks)", card["technical"])

    if is_buyable:
        _render_buy_panel(user_id, row, PAGE_SEARCH, "search")
    else:
        st.info("Signal entry locked until trend / RSI filters clear.")

    # Charts + narratives
    narratives = factors.chart_narratives(row, history if history is not None else pd.DataFrame())
    st.markdown(
        '<div class="ms-section"><h3 class="ms-title">Technical Charts + Readouts</h3>'
        '<p class="ms-muted">Each panel is followed by a short summary of what the market is saying.</p></div>',
        unsafe_allow_html=True,
    )
    if history is None or history.empty:
        st.warning("Chart history unavailable right now.")
    else:
        st.plotly_chart(create_price_sma_chart(history, ticker), use_container_width=True)
        st.info(narratives["price_sma"])
        st.plotly_chart(create_volume_chart(history, ticker), use_container_width=True)
        st.info(narratives["volume"])
        st.plotly_chart(create_rsi_chart(history, ticker), use_container_width=True)
        st.info(narratives["rsi"])
        st.caption(narratives["atr_note"])


def render_validation(user_id: int) -> None:
    render_html("VALIDATION_HEADER")
    if st.session_state.get("order_flash"):
        st.success(st.session_state.order_flash)

    # Always validate on entering this view
    clearances = pipeline.validate_active_signals(user_id)
    if clearances:
        st.info(f"Auto-cleared {len(clearances)} signal(s) on stop/target.")

    scorecard = pipeline.compute_forward_test_scorecard(user_id)

    st.markdown(
        '<div class="ms-section"><h3 class="ms-title">Forward-Test Command Center</h3>'
        '<p class="ms-muted">Track win rate, expectancy, holding horizon and velocity — '
        "this is where checklist conviction is proven.</p></div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_html(
            "METRIC_TILE",
            label="Closed Tests",
            value=str(scorecard["total_signals_tracked"]),
            value_color="#0f172a",
            subtext=f"Open now: {scorecard.get('open_signals', 0)}",
        )
    with c2:
        render_html(
            "METRIC_TILE",
            label="Win Rate",
            value=f"{scorecard['win_rate_pct']:.1f}%",
            value_color="#059669",
            subtext=f"{scorecard.get('successful_trades', 0)} success / {scorecard.get('bad_trades', 0)} bad",
        )
    with c3:
        rupee = scorecard["total_realized_rupee_return"]
        render_html(
            "METRIC_TILE",
            label="Realized ₹ P&L",
            value=f"₹{rupee:,.2f}",
            value_color="#059669" if rupee >= 0 else "#dc2626",
            subtext=f"Expectancy ₹{scorecard.get('expectancy_rupee', 0):,.2f}/trade",
        )
    with c4:
        hold = scorecard.get("avg_hold_days")
        render_html(
            "METRIC_TILE",
            label="Avg Hold Horizon",
            value=("—" if hold is None else f"{hold:.1f}d"),
            value_color="#0f172a",
            subtext="Entry → exit days",
        )

    vel = scorecard.get("velocity_buckets") or {}
    st.caption(
        f"Velocity mix — Fast: **{vel.get('FAST', 0)}** · Normal: **{vel.get('NORMAL', 0)}** · "
        f"Slow: **{vel.get('SLOW', 0)}** · Other: **{vel.get('OTHER', 0)}**"
    )
    buckets = scorecard.get("return_buckets") or {}
    if scorecard["total_signals_tracked"] > 0:
        st.caption(
            f"Return terciles — Higher: **{buckets.get('high_return', 0)}** · "
            f"Mid: **{buckets.get('mid_return', 0)}** · Lower: **{buckets.get('low_return', 0)}** "
            "(basis for future score-bucket win-rate once entry scores are stored)."
        )

    if st.button("Refresh", type="primary", use_container_width=True, key="force_validate"):
        with st.spinner("Validate open signals against latest prices…"):
            st.session_state.sync_result = pipeline.refresh_verified_live(user_id=user_id)
        st.rerun()

    left, right = st.columns(2)
    with left:
        st.markdown('<h3 class="ms-title">Active Signals</h3>', unsafe_allow_html=True)
        positions = db.get_active_positions(user_id)
        if positions is None or positions.empty:
            st.caption("No active signals.")
        else:
            positions = positions[positions["user_id"] == user_id] if "user_id" in positions.columns else positions
            rows = []
            for _, p in positions.iterrows():
                entry = float(p["entry_price"])
                mark = float(p["current_price"] or entry)
                stop = float(p["stop_loss"])
                tgt = float(p["target"])
                # rough progress to target / risk
                risk = max(entry - stop, 1e-6)
                reward_span = max(tgt - entry, 1e-6)
                prog = max(0.0, min(1.0, (mark - entry) / reward_span)) if mark >= entry else 0.0
                rows.append([
                    p["ticker"],
                    f"₹{entry:,.2f}",
                    f"₹{mark:,.2f}",
                    f"₹{stop:,.2f}",
                    f"₹{tgt:,.2f}",
                    f"{prog * 100:.0f}%",
                    f"₹{float(p['unrealized_pnl'] or 0):,.2f}",
                ])
            render_borderless_table(
                ["Ticker", "Entry", "Mark", "Stop", "Target", "To-target", "uPnL"],
                rows,
                height=300,
            )

    with right:
        st.markdown('<h3 class="ms-title">Closed Results</h3>', unsafe_allow_html=True)
        trades = scorecard.get("trades") or []
        if not trades:
            st.caption("No completed forward-tests yet — take Screener buys to build your real success ratio.")
        else:
            rows = []
            for t in trades:
                rows.append([
                    t["ticker"],
                    t["exit_status"],
                    f"₹{t['absolute_delta']:,.2f}",
                    f"{t['pct_return']:.2f}%",
                    t["velocity_label"],
                ])
            render_borderless_table(
                ["Ticker", "Result", "Abs Δ ₹", "% Return", "Velocity"],
                rows,
                height=300,
            )

            st.markdown('<div class="ms-section"><h3 class="ms-title">Trade Deep-Dive</h3></div>', unsafe_allow_html=True)
            for t in trades[:8]:
                status = str(t["exit_status"]).upper()
                badge_html = (
                    extract_html_block("BADGE_SUCCESS")
                    if status == db.EXIT_SUCCESS.upper()
                    else extract_html_block("BADGE_BAD")
                )
                delta_color = "#059669" if t["absolute_delta"] >= 0 else "#dc2626"
                st.markdown(
                    f"""
                    <div class="ms-section">
                      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.75rem;flex-wrap:wrap;">
                        <strong class="ticker" style="font-size:1rem;">{html.escape(t['ticker'])}</strong>
                        {badge_html}
                      </div>
                      <div class="ms-grid" style="margin-top:0.55rem;">
                        <div><div class="ms-kv__k">Absolute Value Delta</div>
                          <div class="ms-kv__v" style="color:{delta_color};">₹{t['absolute_delta']:,.2f}</div></div>
                        <div><div class="ms-kv__k">% P/L Return</div>
                          <div class="ms-kv__v" style="color:{delta_color};">{t['pct_return']:.2f}%</div></div>
                        <div><div class="ms-kv__k">Velocity</div>
                          <div class="ms-kv__v">{html.escape(t['velocity_label'])}</div></div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def main() -> None:
    st.set_page_config(
        page_title="Medallion Swing — Forward-Test",
        page_icon="🪐",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    runtime = prod_runtime.configure_runtime()
    prod_runtime.maybe_restore_seed_db(db.DATABASE_PATH)
    db.init_database()
    init_session_state()
    inject_theme()

    if not st.session_state.logged_in:
        render_login_gate()
        return

    user_id = int(st.session_state.user_id)
    # After login: never auto-fill. Only validate open signals if DB already has rows.
    if st.session_state.get("sync_result") is None:
        if db.leaderboard_count() > 0:
            with st.spinner("Validating open forward-test signals…"):
                run_light_validate(user_id)
        else:
            st.session_state.sync_result = {
                "message": "Click Refresh on Screener for live 3-site verified data.",
                "clearances": [],
            }

    page = st.session_state.nav_page
    render_top_navbar(page, st.session_state.username or "")
    sync_msg = ""
    if st.session_state.get("sync_result"):
        sync_msg = f" · {st.session_state.sync_result.get('message', '')}"
    ready_n = len(pipeline.filter_display_ready(db.get_leaderboard(limit=500)))
    host = "cloud" if runtime.get("cloud") else "local"
    st.caption(
        f"user_id `{user_id}` · Forward-test qty **1** · "
        f"Market: **live NSE** · host **{host}** · "
        f"verified ready **{ready_n}**{sync_msg}"
    )

    if page == PAGE_SCREENER:
        render_screener(user_id)
    elif page == PAGE_SEARCH:
        render_search(user_id)
    else:
        render_validation(user_id)


if __name__ == "__main__":
    main()
