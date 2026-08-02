"""
Medallion Swing — Forward-Test Validation App Controller
Fixed Quantity = 1 · Top navbar · Borderless HTML tables · No capital ledger
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, List, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

import data_pipeline as pipeline
import database_engine as db

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


def run_signal_sync(user_id: int) -> None:
    st.session_state.sync_result = pipeline.sync_user_and_screener_data(user_id)


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
            run_signal_sync(int(st.session_state.user_id))
            st.rerun()
    with c2:
        if st.button("Search Profile", use_container_width=True, key="nav_search"):
            st.session_state.nav_page = PAGE_SEARCH
            run_signal_sync(int(st.session_state.user_id))
            st.rerun()
    with c3:
        if st.button("Forward-Test", use_container_width=True, key="nav_val"):
            st.session_state.nav_page = PAGE_VALIDATION
            run_signal_sync(int(st.session_state.user_id))
            st.rerun()
    with c4:
        if st.button("Log Out", use_container_width=True, key="nav_logout"):
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
        run_signal_sync(int(user_id))
        st.success(message)
        st.rerun()

    ok, message, user_id = db.verify_user(username, password)
    if not ok:
        st.error(message)
        return
    st.session_state.logged_in = True
    st.session_state.user_id = user_id
    st.session_state.username = username.strip()
    run_signal_sync(int(user_id))
    st.success(message)
    st.rerun()


def create_technical_chart(df_price: pd.DataFrame, ticker: str) -> go.Figure:
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


def _render_buy_panel(user_id: int, row: pd.Series, source_page: str, prefix: str) -> None:
    ticker = str(row["ticker"]).upper()
    close_price = float(row["close_price"])
    atr = float(row["atr_value"])
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


def render_screener(user_id: int) -> None:
    render_html("BANNER")
    if st.session_state.get("order_flash"):
        st.success(st.session_state.order_flash)

    st.markdown(
        '<div class="ms-section"><h2 class="ms-title">Smart Screener</h2>'
        '<p class="ms-muted">Live NSE universe · fundamentals from Screener.in · buys track exactly 1 share.</p></div>',
        unsafe_allow_html=True,
    )

    df = db.get_leaderboard(limit=100)
    if df is None or df.empty:
        with st.spinner("Loading live NSE screener universe (first sync can take 1–3 minutes)…"):
            st.session_state.sync_result = pipeline.sync_user_and_screener_data(user_id, force=True)
        df = db.get_leaderboard(limit=100)
    if df is None or df.empty:
        st.error("Live NSE screener is empty right now. Click refresh on Forward-Test or try again shortly.")
        return

    display = df.copy()
    display["composite_score"] = pd.to_numeric(display["composite_score"], errors="coerce")
    display = display.dropna(subset=["composite_score"]).sort_values("composite_score", ascending=False)

    rows = []
    for _, r in display.iterrows():
        rows.append([
            r["ticker"],
            r["company_name"],
            r["sector"],
            f"{float(r['fundamental_score']):.0f}",
            f"{float(r['technical_score']):.0f}",
            f"{float(r['composite_score']):.0f}",
            f"₹{float(r['close_price']):,.2f}",
            "Yes" if int(r.get("is_buyable", 0)) else "No",
        ])
    render_borderless_table(
        ["Ticker", "Company", "Sector", "Fund.", "Tech.", "Score", "CMP", "Buyable"],
        rows,
        height=340,
    )

    tickers = display["ticker"].astype(str).tolist()
    default_ix = 0
    if st.session_state.selected_ticker in tickers:
        default_ix = tickers.index(st.session_state.selected_ticker)
    selected = st.selectbox("Select ticker", options=tickers, index=default_ix)
    st.session_state.selected_ticker = selected
    row = display[display["ticker"] == selected].iloc[0]

    close_price = float(row["close_price"])
    levels = pipeline.build_trade_levels(close_price, float(row["atr_value"]))
    is_buyable, reason = pipeline.check_buyability(row)
    badge = extract_html_block("BADGE_BUY" if is_buyable else "BADGE_HOLD")
    render_html(
        "ASSET_HEADER",
        company_name=row.get("company_name", selected),
        ticker=selected,
        description=row.get("description", ""),
        sector=row.get("sector", "—"),
        industry=row.get("industry", "—"),
        decision_badge=badge,
    )
    if not is_buyable and "OVEREXTENDED" in reason:
        st.markdown(f'<div class="ms-warning">{reason}</div>', unsafe_allow_html=True)
    else:
        st.caption(reason)

    render_html(
        "TRADE_PARAMS",
        ticker=selected,
        cmp=f"{close_price:,.2f}",
        stop_loss=f"{levels['stop_loss']:,.2f}",
        target=f"{levels['target']:,.2f}",
        rrr=f"{levels['rrr']:.2f}",
    )
    sma_trend = "Above 200 SMA" if close_price > float(row.get("sma_200", 0)) else "Below 200 SMA"
    render_html(
        "REPORT_CARD",
        ticker=selected,
        roic=f"{float(row.get('roic', 0)):.1f}",
        net_debt_ebitda=f"{float(row.get('net_debt_ebitda', 0)):.2f}",
        peg=f"{float(row.get('peg_ratio', 0)):.2f}",
        interest_coverage=f"{float(row.get('interest_coverage', 0)):.1f}",
        promoter_pledge=f"{float(row.get('promoter_pledge_pct', 0)):.1f}",
        profit_growth=f"{float(row.get('yoy_profit_growth', 0)):.1f}",
        sma_trend=sma_trend,
        rsi=f"{float(row.get('rsi_14', 0)):.1f}",
        delivery_pct=f"{float(row.get('delivery_pct_10d', 0)):.1f}",
        composite_score=f"{float(row.get('composite_score', 0)):.0f}",
    )
    if is_buyable:
        _render_buy_panel(user_id, row, PAGE_SCREENER, "screen")
    else:
        st.info("Signal entry locked until trend / RSI filters clear.")


def render_search(user_id: int) -> None:
    render_html("BANNER")
    if st.session_state.get("order_flash"):
        st.success(st.session_state.order_flash)
    st.markdown(
        '<div class="ms-section"><h2 class="ms-title">Search Profile</h2>'
        '<p class="ms-muted">Lookup a ticker. Cleared signals open at fixed Quantity = 1.</p></div>',
        unsafe_allow_html=True,
    )
    ticker_input = st.text_input("Ticker", placeholder="TCS, RELIANCE, INFY, HDFCBANK")
    if not ticker_input:
        return
    ticker = ticker_input.strip().upper()
    with st.spinner(f"Fetching live NSE profile for {ticker}…"):
        row = pipeline.ensure_ticker_live(ticker, include_fundamentals=True)
    if row is None:
        st.error(f"No live NSE listing for '{ticker}'. Try the NSE symbol (e.g. TCS, RELIANCE).")
        return

    close_price = float(row["close_price"])
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
        _render_buy_panel(user_id, row, PAGE_SEARCH, "search")

    st.markdown(
        '<div class="ms-section"><h3 class="ms-title">Technical Chart</h3>'
        '<p class="ms-muted">Live NSE daily OHLC (Yahoo/NSE feed)</p></div>',
        unsafe_allow_html=True,
    )
    history = pipeline.generate_price_history(ticker, close_price, 250)
    if history is None or history.empty:
        st.warning("Chart history unavailable right now.")
    else:
        st.plotly_chart(create_technical_chart(history, ticker), use_container_width=True)


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
        '<div class="ms-section"><h3 class="ms-title">Global Strategy Diagnostic Scorecard</h3></div>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        render_html(
            "METRIC_TILE",
            label="Total Signals Tracked",
            value=str(scorecard["total_signals_tracked"]),
            value_color="#0f172a",
            subtext="Closed forward-tests",
        )
    with c2:
        render_html(
            "METRIC_TILE",
            label="Strategy Win Rate",
            value=f"{scorecard['win_rate_pct']:.1f}%",
            value_color="#059669",
            subtext="Successful / Total",
        )
    with c3:
        rupee = scorecard["total_realized_rupee_return"]
        render_html(
            "METRIC_TILE",
            label="Total Realized Absolute ₹ Return",
            value=f"₹{rupee:,.2f}",
            value_color="#059669" if rupee >= 0 else "#dc2626",
            subtext="Sum of 1-share P&L",
        )

    if st.button("Refresh Quotes & Validate Signals", use_container_width=True, key="force_validate"):
        with st.spinner("Force-refreshing live NSE quotes…"):
            st.session_state.sync_result = pipeline.sync_user_and_screener_data(user_id, force=True)
        st.rerun()

    left, right = st.columns(2)
    with left:
        st.markdown('<h3 class="ms-title">Active Signals Monitor</h3>', unsafe_allow_html=True)
        positions = db.get_active_positions(user_id)
        if positions is None or positions.empty:
            st.caption("No active signals.")
        else:
            positions = positions[positions["user_id"] == user_id]
            rows = []
            for _, p in positions.iterrows():
                rows.append([
                    p["ticker"],
                    f"₹{float(p['entry_price']):,.2f}",
                    f"₹{float(p['current_price'] or p['entry_price']):,.2f}",
                    f"₹{float(p['stop_loss']):,.2f}",
                    f"₹{float(p['target']):,.2f}",
                    int(p["quantity"]),
                    f"₹{float(p['unrealized_pnl'] or 0):,.2f}",
                ])
            render_borderless_table(
                ["Ticker", "Entry", "Mark", "Stop", "Target", "Qty", "uPnL"],
                rows,
                height=280,
            )

    with right:
        st.markdown('<h3 class="ms-title">Closed Signal Results</h3>', unsafe_allow_html=True)
        trades = scorecard.get("trades") or []
        if not trades:
            st.caption("No completed forward-tests yet.")
        else:
            rows = []
            for t in trades:
                badge = t["exit_status"]
                rows.append([
                    t["ticker"],
                    badge,
                    f"₹{t['absolute_delta']:,.2f}",
                    f"{t['pct_return']:.2f}%",
                    t["velocity_label"],
                ])
            render_borderless_table(
                ["Ticker", "Classification", "Abs Δ ₹", "% Return", "Velocity"],
                rows,
                height=280,
            )

            # Detail capsules for first few trades
            st.markdown('<div class="ms-section"><h3 class="ms-title">Deep-Dive Metrics</h3></div>', unsafe_allow_html=True)
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
    db.init_database()
    init_session_state()
    inject_theme()

    if not st.session_state.logged_in:
        render_login_gate()
        return

    user_id = int(st.session_state.user_id)
    if st.session_state.get("sync_result") is None:
        with st.spinner("Syncing live NSE quotes & fundamentals…"):
            run_signal_sync(user_id)

    page = st.session_state.nav_page
    render_top_navbar(page, st.session_state.username or "")
    sync_msg = ""
    if st.session_state.get("sync_result"):
        sync_msg = f" · {st.session_state.sync_result.get('message', '')}"
    st.caption(
        f"user_id `{user_id}` · Forward-test qty **1** · "
        f"Market: **live NSE**{sync_msg}"
    )

    if page == PAGE_SCREENER:
        render_screener(user_id)
    elif page == PAGE_SEARCH:
        render_search(user_id)
    else:
        render_validation(user_id)


if __name__ == "__main__":
    main()
