"""
Medallion Swing — Forward-Test Validation Data Engine
Multi-user signal tracking at fixed Quantity = 1. No capital ledger.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

DATABASE_PATH = os.environ.get(
    "MEDALLION_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "medallion_system.db"),
)
_DB_LOCK = threading.RLock()
FIXED_QUANTITY = 1
IST = timezone(timedelta(hours=5, minutes=30))
META_SCREENER_AS_OF = "screener_as_of_date"
META_SCREENER_STATUS = "screener_refresh_status"  # idle | running | complete | failed
META_SCREENER_MSG = "screener_refresh_message"
META_REFRESH_T0 = "screener_refresh_t0"
META_REFRESH_COMPLETE0 = "screener_refresh_complete0"
LOAD_MAX_RETRIES = 3

EXIT_SUCCESS = "SUCCESSFUL TRADE"
EXIT_BAD = "BAD TRADE"
EXIT_MANUAL = "MANUAL EXIT"

MOCK_LEADERBOARD: List[Dict[str, Any]] = [
    {
        "ticker": "HDFCBANK",
        "company_name": "HDFC Bank Limited",
        "description": "Premium banking franchise with dominant retail and wholesale market position.",
        "sector": "Financial Services",
        "industry": "Banking",
        "composite_score": 91.0,
        "fundamental_score": 46.0,
        "technical_score": 45.0,
        "close_price": 1892.45,
        "atr_value": 68.40,
        "is_buyable": 1,
        "roic": 17.3,
        "net_debt_ebitda": 1.2,
        "peg_ratio": 1.22,
        "interest_coverage": 6.3,
        "promoter_pledge_pct": 4.5,
        "yoy_profit_growth": 19.2,
        "sma_50": 1820.00,
        "sma_200": 1750.00,
        "rsi_14": 56.3,
        "delivery_pct_10d": 48.3,
        "alpha_3m": 25.5,
    },
    {
        "ticker": "TCS",
        "company_name": "Tata Consultancy Services",
        "description": "Global IT services and consulting powerhouse with durable free-cash conversion.",
        "sector": "Information Technology",
        "industry": "IT Services",
        "composite_score": 90.0,
        "fundamental_score": 48.0,
        "technical_score": 42.0,
        "close_price": 3650.50,
        "atr_value": 85.25,
        "is_buyable": 1,
        "roic": 18.5,
        "net_debt_ebitda": 1.8,
        "peg_ratio": 1.15,
        "interest_coverage": 4.2,
        "promoter_pledge_pct": 3.2,
        "yoy_profit_growth": 18.5,
        "sma_50": 3580.00,
        "sma_200": 3450.00,
        "rsi_14": 58.5,
        "delivery_pct_10d": 45.2,
        "alpha_3m": 22.5,
    },
    {
        "ticker": "RELIANCE",
        "company_name": "Reliance Industries",
        "description": "Integrated oil, gas, retail, and digital conglomerate with diversified cash flows.",
        "sector": "Energy",
        "industry": "Oil & Gas",
        "composite_score": 89.0,
        "fundamental_score": 45.0,
        "technical_score": 44.0,
        "close_price": 1245.30,
        "atr_value": 32.50,
        "is_buyable": 1,
        "roic": 14.2,
        "net_debt_ebitda": 2.1,
        "peg_ratio": 1.28,
        "interest_coverage": 3.5,
        "promoter_pledge_pct": 5.1,
        "yoy_profit_growth": 12.3,
        "sma_50": 1210.00,
        "sma_200": 1150.00,
        "rsi_14": 55.2,
        "delivery_pct_10d": 38.5,
        "alpha_3m": 8.3,
    },
    {
        "ticker": "INFY",
        "company_name": "Infosys Limited",
        "description": "Leading software services company with global enterprise delivery footprint.",
        "sector": "Information Technology",
        "industry": "IT Services",
        "composite_score": 88.0,
        "fundamental_score": 47.0,
        "technical_score": 41.0,
        "close_price": 2880.75,
        "atr_value": 95.60,
        "is_buyable": 1,
        "roic": 16.8,
        "net_debt_ebitda": 0.9,
        "peg_ratio": 1.05,
        "interest_coverage": 8.1,
        "promoter_pledge_pct": 2.8,
        "yoy_profit_growth": 16.8,
        "sma_50": 2850.00,
        "sma_200": 2700.00,
        "rsi_14": 52.8,
        "delivery_pct_10d": 52.1,
        "alpha_3m": 18.2,
    },
    {
        "ticker": "ITC",
        "company_name": "ITC Limited",
        "description": "Diversified FMCG and hotels franchise with resilient cash generation.",
        "sector": "Consumer Staples",
        "industry": "FMCG",
        "composite_score": 82.0,
        "fundamental_score": 43.0,
        "technical_score": 39.0,
        "close_price": 448.20,
        "atr_value": 8.75,
        "is_buyable": 1,
        "roic": 22.1,
        "net_debt_ebitda": 0.2,
        "peg_ratio": 1.35,
        "interest_coverage": 28.0,
        "promoter_pledge_pct": 0.0,
        "yoy_profit_growth": 11.4,
        "sma_50": 442.00,
        "sma_200": 420.00,
        "rsi_14": 54.0,
        "delivery_pct_10d": 55.0,
        "alpha_3m": 6.2,
    },
    {
        "ticker": "SBIN",
        "company_name": "State Bank of India",
        "description": "Systemically important public-sector bank with broad deposit franchise.",
        "sector": "Financial Services",
        "industry": "Banking",
        "composite_score": 78.0,
        "fundamental_score": 40.0,
        "technical_score": 38.0,
        "close_price": 812.60,
        "atr_value": 18.40,
        "is_buyable": 0,
        "roic": 12.8,
        "net_debt_ebitda": 1.6,
        "peg_ratio": 1.10,
        "interest_coverage": 4.8,
        "promoter_pledge_pct": 0.0,
        "yoy_profit_growth": 14.5,
        "sma_50": 790.00,
        "sma_200": 820.00,
        "rsi_14": 48.5,
        "delivery_pct_10d": 41.2,
        "alpha_3m": 3.1,
    },
]


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def today_ist() -> str:
    """Trading calendar day in India (YYYY-MM-DD)."""
    return datetime.now(IST).strftime("%Y-%m-%d")


def get_meta(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
            row = cursor.execute(
                "SELECT value FROM app_meta WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row and row["value"] is not None else default
    except Exception as exc:
        logger.error("get_meta failed: %s", exc)
        return default


def set_meta(key: str, value: str) -> None:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO app_meta (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), _now_iso()),
            )
    except Exception as exc:
        logger.error("set_meta failed: %s", exc)


def screener_as_of() -> Optional[str]:
    return get_meta(META_SCREENER_AS_OF)


def screener_is_today() -> bool:
    as_of = screener_as_of()
    return bool(as_of) and as_of == today_ist()


def set_screener_refresh_state(
    *,
    as_of: Optional[str] = None,
    status: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    if as_of is not None:
        set_meta(META_SCREENER_AS_OF, as_of)
    if status is not None:
        set_meta(META_SCREENER_STATUS, status)
    if message is not None:
        set_meta(META_SCREENER_MSG, message)


def _ensure_load_attempts_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS screener_load_attempts (
            ticker TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            exhausted INTEGER DEFAULT 0,
            updated_at TIMESTAMP
        )
        """
    )


def clear_load_attempts() -> None:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_load_attempts_table(cursor)
            cursor.execute("DELETE FROM screener_load_attempts")
    except Exception as exc:
        logger.error("clear_load_attempts failed: %s", exc)


def record_load_attempt(ticker: str, *, ok: bool, error: str = "") -> Dict[str, Any]:
    """Increment attempts on failure; clear row on success. Exhaust after LOAD_MAX_RETRIES."""
    sym = str(ticker or "").strip().upper()
    if not sym:
        return {"ticker": sym, "attempts": 0, "exhausted": False}
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_load_attempts_table(cursor)
            if ok:
                cursor.execute("DELETE FROM screener_load_attempts WHERE ticker = ?", (sym,))
                return {"ticker": sym, "attempts": 0, "exhausted": False}
            row = cursor.execute(
                "SELECT attempts FROM screener_load_attempts WHERE ticker = ?", (sym,)
            ).fetchone()
            attempts = int(row["attempts"]) + 1 if row else 1
            exhausted = 1 if attempts >= LOAD_MAX_RETRIES else 0
            cursor.execute(
                """
                INSERT INTO screener_load_attempts (ticker, attempts, last_error, exhausted, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    attempts=excluded.attempts,
                    last_error=excluded.last_error,
                    exhausted=excluded.exhausted,
                    updated_at=excluded.updated_at
                """,
                (sym, attempts, (error or "")[:240], exhausted, _now_iso()),
            )
            return {"ticker": sym, "attempts": attempts, "exhausted": bool(exhausted), "error": error}
    except Exception as exc:
        logger.error("record_load_attempt failed: %s", exc)
        return {"ticker": sym, "attempts": 0, "exhausted": False}


def list_exhausted_load_failures(limit: int = 200) -> List[Dict[str, Any]]:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_load_attempts_table(cursor)
            rows = cursor.execute(
                """
                SELECT ticker, attempts, last_error, updated_at
                FROM screener_load_attempts
                WHERE COALESCE(exhausted, 0) = 1
                ORDER BY ticker
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [
                {
                    "ticker": str(r["ticker"]).upper(),
                    "attempts": int(r["attempts"] or 0),
                    "error": r["last_error"] or "fetch failed",
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
    except Exception as exc:
        logger.error("list_exhausted_load_failures failed: %s", exc)
        return []


def exhausted_ticker_set() -> set:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_load_attempts_table(cursor)
            rows = cursor.execute(
                "SELECT ticker FROM screener_load_attempts WHERE COALESCE(exhausted, 0) = 1"
            ).fetchall()
            return {str(r["ticker"]).upper() for r in rows}
    except Exception:
        return set()


def start_refresh_eta_clock(complete0: int = 0) -> None:
    import time as _time

    set_meta(META_REFRESH_T0, str(_time.time()))
    set_meta(META_REFRESH_COMPLETE0, str(int(complete0)))


def compute_refresh_eta_minutes(complete_now: int, target: int) -> Optional[float]:
    """
    ETA from wall-clock since refresh start.
    Ignore the noisy first 1–2 completions (cold start), and floor the rate so
    early ETA does not explode to multi-hour nonsense.
    """
    import time as _time

    t0_raw = get_meta(META_REFRESH_T0)
    c0_raw = get_meta(META_REFRESH_COMPLETE0, "0")
    if not t0_raw:
        return None
    try:
        t0 = float(t0_raw)
        c0 = int(float(c0_raw or 0))
    except (TypeError, ValueError):
        return None
    elapsed = max(_time.time() - t0, 1.0)
    gained = max(0, int(complete_now) - c0)
    remaining = max(0, int(target) - int(complete_now))
    if remaining <= 0:
        return 0.0
    if gained < 3:
        # Warm-up: assume ~8 ready/min once parallel load is humming
        return round(remaining / 8.0, 1)
    rate_per_min = gained / elapsed * 60.0
    # Floor: parallel path should not be slower than ~4/min in steady state
    rate_per_min = max(rate_per_min, 4.0)
    if rate_per_min <= 0:
        return None
    return round(remaining / rate_per_min, 1)


@contextmanager
def get_connection(timeout: float = 30.0):
    conn = None
    acquired = False
    try:
        _DB_LOCK.acquire()
        acquired = True
        conn = sqlite3.connect(DATABASE_PATH, timeout=timeout, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if acquired:
            _DB_LOCK.release()


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    )
    return cursor.fetchone() is not None


def _table_columns(cursor: sqlite3.Cursor, table: str) -> List[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return [str(row[1]) for row in cursor.fetchall()]


def _migrate_schema(cursor: sqlite3.Cursor) -> None:
    # Portfolio capital concepts are obsolete for forward-test mode
    cursor.execute("DROP TABLE IF EXISTS portfolio_ledger")
    cursor.execute("DROP TABLE IF EXISTS capital_flows")

    if _table_exists(cursor, "active_positions"):
        cols = _table_columns(cursor, "active_positions")
        if "user_id" not in cols or "entry_timestamp" not in cols:
            cursor.execute("DROP TABLE IF EXISTS active_positions")
    if _table_exists(cursor, "closed_trades_history"):
        cols = _table_columns(cursor, "closed_trades_history")
        if "user_id" not in cols or "exit_status" not in cols or "entry_timestamp" not in cols:
            cursor.execute("DROP TABLE IF EXISTS closed_trades_history")
    if _table_exists(cursor, "screener_leaderboard"):
        cols = _table_columns(cursor, "screener_leaderboard")
        if "close_price" not in cols or "atr_value" not in cols:
            cursor.execute("DROP TABLE IF EXISTS screener_leaderboard")


def _ensure_leaderboard_extra_columns(cursor: sqlite3.Cursor) -> None:
    """Additive migrations for quality / PE columns."""
    cols = set(_table_columns(cursor, "screener_leaderboard"))
    additions = {
        "pe_ratio": "REAL",
        "roe": "REAL",
        "data_quality": "TEXT",
        "fundamentals_verified": "INTEGER DEFAULT 0",
        "sources_ok_count": "INTEGER DEFAULT 0",
        "ohlcv_ready": "INTEGER DEFAULT 0",
        "price_source": "TEXT",
        "price_kind": "TEXT",
        "prev_close": "REAL",
    }
    for name, decl in additions.items():
        if name not in cols:
            cursor.execute(f"ALTER TABLE screener_leaderboard ADD COLUMN {name} {decl}")


def init_database() -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _migrate_schema(cursor)

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screener_leaderboard (
                    ticker TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    description TEXT,
                    sector TEXT,
                    industry TEXT,
                    composite_score REAL,
                    fundamental_score REAL,
                    technical_score REAL,
                    close_price REAL,
                    atr_value REAL,
                    is_buyable INTEGER DEFAULT 0,
                    last_updated TIMESTAMP,
                    roic REAL,
                    net_debt_ebitda REAL,
                    peg_ratio REAL,
                    interest_coverage REAL,
                    promoter_pledge_pct REAL,
                    yoy_profit_growth REAL,
                    sma_50 REAL,
                    sma_200 REAL,
                    rsi_14 REAL,
                    delivery_pct_10d REAL,
                    alpha_3m REAL,
                    pe_ratio REAL,
                    data_quality TEXT,
                    fundamentals_verified INTEGER DEFAULT 0,
                    sources_ok_count INTEGER DEFAULT 0
                )
                """
            )
            _ensure_leaderboard_extra_columns(cursor)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screener_load_attempts (
                    ticker TEXT PRIMARY KEY,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    exhausted INTEGER DEFAULT 0,
                    updated_at TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS active_positions (
                    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    entry_timestamp TIMESTAMP NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target REAL NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    current_price REAL,
                    unrealized_pnl REAL DEFAULT 0.0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS closed_trades_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    entry_timestamp TIMESTAMP,
                    exit_timestamp TIMESTAMP,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    final_pnl REAL,
                    exit_reason TEXT,
                    exit_status TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )

            cursor.execute("SELECT COUNT(*) AS cnt FROM screener_leaderboard")
            if int(cursor.fetchone()["cnt"]) == 0:
                # Seed mock only in offline/test mode; live mode fills via NSE sync.
                market_mode = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
                if market_mode in {"mock", "offline", "test"}:
                    _seed_mock_leaderboard(cursor)
        return True
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
        return False


def _seed_mock_leaderboard(cursor: sqlite3.Cursor) -> None:
    stamp = _now_iso()
    _ensure_leaderboard_extra_columns(cursor)
    for row in MOCK_LEADERBOARD:
        cursor.execute(
            """
            INSERT OR IGNORE INTO screener_leaderboard (
                ticker, company_name, description, sector, industry,
                composite_score, fundamental_score, technical_score,
                close_price, atr_value, is_buyable, last_updated,
                roic, net_debt_ebitda, peg_ratio, interest_coverage,
                promoter_pledge_pct, yoy_profit_growth, sma_50, sma_200,
                rsi_14, delivery_pct_10d, alpha_3m,
                pe_ratio, data_quality, fundamentals_verified, sources_ok_count, ohlcv_ready
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["ticker"], row["company_name"], row["description"], row["sector"], row["industry"],
                row["composite_score"], row["fundamental_score"], row["technical_score"],
                row["close_price"], row["atr_value"], row["is_buyable"], stamp,
                row["roic"], row["net_debt_ebitda"], row["peg_ratio"], row["interest_coverage"],
                row["promoter_pledge_pct"], row["yoy_profit_growth"], row["sma_50"], row["sma_200"],
                row["rsi_14"], row["delivery_pct_10d"], row["alpha_3m"],
                row.get("pe_ratio") or 18.0,
                "VERIFIED",
                1,
                3,
                1,
            ),
        )


def leaderboard_is_empty() -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM screener_leaderboard")
            return int(cursor.fetchone()["cnt"]) == 0
    except Exception as exc:
        logger.error("leaderboard_is_empty failed: %s", exc)
        return True


def leaderboard_count() -> int:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM screener_leaderboard")
            return int(cursor.fetchone()["cnt"])
    except Exception as exc:
        logger.error("leaderboard_count failed: %s", exc)
        return 0


def list_leaderboard_tickers() -> List[str]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT ticker FROM screener_leaderboard ORDER BY ticker"
            ).fetchall()
            return [str(r["ticker"]).upper() for r in rows]
    except Exception as exc:
        logger.error("list_leaderboard_tickers failed: %s", exc)
        return []


def tickers_missing_fundamentals(limit: int = 50) -> List[str]:
    """Names in DB that are not yet 3-site fundamentals-verified."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_leaderboard_extra_columns(cursor)
            rows = cursor.execute(
                """
                SELECT ticker FROM screener_leaderboard
                WHERE COALESCE(fundamentals_verified, 0) = 0
                   OR COALESCE(data_quality, '') != 'VERIFIED'
                   OR (
                        (roic IS NULL OR roic = 0)
                    AND (pe_ratio IS NULL OR pe_ratio = 0)
                    AND (peg_ratio IS NULL OR peg_ratio = 0)
                   )
                ORDER BY ticker
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [str(r["ticker"]).upper() for r in rows]
    except Exception as exc:
        logger.error("tickers_missing_fundamentals failed: %s", exc)
        return []


def fundamentals_coverage() -> Dict[str, int]:
    """How many leaderboard rows have verified fundamentals."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_leaderboard_extra_columns(cursor)
            total = int(
                cursor.execute("SELECT COUNT(*) AS c FROM screener_leaderboard").fetchone()["c"]
            )
            verified = int(
                cursor.execute(
                    """
                    SELECT COUNT(*) AS c FROM screener_leaderboard
                    WHERE COALESCE(fundamentals_verified, 0) = 1
                      AND COALESCE(data_quality, '') = 'VERIFIED'
                    """
                ).fetchone()["c"]
            )
            ohlcv = int(
                cursor.execute(
                    "SELECT COUNT(*) AS c FROM screener_leaderboard WHERE COALESCE(ohlcv_ready, 0) = 1"
                ).fetchone()["c"]
            )
            complete = int(
                cursor.execute(
                    """
                    SELECT COUNT(*) AS c FROM screener_leaderboard
                    WHERE COALESCE(close_price, 0) > 0
                      AND COALESCE(fundamentals_verified, 0) = 1
                      AND COALESCE(data_quality, '') = 'VERIFIED'
                      AND COALESCE(ohlcv_ready, 0) = 1
                      AND COALESCE(sources_ok_count, 0) >= 3
                    """
                ).fetchone()["c"]
            )
            return {
                "total": total,
                "verified": verified,
                "missing": max(0, total - verified),
                "ohlcv": ohlcv,
                "complete": complete,
            }
    except Exception as exc:
        logger.error("fundamentals_coverage failed: %s", exc)
        return {"total": 0, "verified": 0, "missing": 0, "ohlcv": 0, "complete": 0}


def tickers_missing_full_data(limit: int = 50) -> List[str]:
    """Names missing price, 3-site fundamentals, or OHLCV technicals."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_leaderboard_extra_columns(cursor)
            rows = cursor.execute(
                """
                SELECT ticker FROM screener_leaderboard
                WHERE COALESCE(close_price, 0) <= 0
                   OR COALESCE(fundamentals_verified, 0) = 0
                   OR COALESCE(data_quality, '') != 'VERIFIED'
                   OR COALESCE(ohlcv_ready, 0) = 0
                   OR COALESCE(sources_ok_count, 0) < 3
                ORDER BY ticker
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [str(r["ticker"]).upper() for r in rows]
    except Exception as exc:
        logger.error("tickers_missing_full_data failed: %s", exc)
        return []


def clear_leaderboard() -> int:
    """Remove all screener rows (use before a clean live swing-universe load)."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM screener_leaderboard")
            n = int(cursor.fetchone()["cnt"])
            cursor.execute("DELETE FROM screener_leaderboard")
            return n
    except Exception as exc:
        logger.error("clear_leaderboard failed: %s", exc)
        return 0


def ensure_mock_leaderboard() -> None:
    """Offline/test seed ONLY when MEDALLION_MARKET_MODE=mock|offline|test."""
    market_mode = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
    if market_mode not in {"mock", "offline", "test"}:
        return
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM screener_leaderboard")
            if int(cursor.fetchone()["cnt"]) == 0:
                _seed_mock_leaderboard(cursor)
    except Exception as exc:
        logger.error("ensure_mock_leaderboard failed: %s", exc)


def register_user(username: str, password: str) -> Tuple[bool, str, Optional[int]]:
    username = (username or "").strip()
    if len(username) < 3:
        return False, "Username must be at least 3 characters.", None
    if len(password or "") < 6:
        return False, "Password must be at least 6 characters.", None
    salt = secrets.token_hex(16)
    password_hash = f"{salt}${_hash_password(password, salt)}"
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _now_iso()),
            )
            user_id = int(cursor.lastrowid)
        return True, "Account created. Forward-test engine ready — each signal tracks exactly 1 share.", user_id
    except sqlite3.IntegrityError:
        return False, "Username already exists. Please choose another.", None
    except Exception as exc:
        logger.error("register_user failed: %s", exc)
        return False, f"Registration failed: {exc}", None


def verify_user(username: str, password: str) -> Tuple[bool, str, Optional[int]]:
    username = (username or "").strip()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, password_hash FROM users WHERE username = ?",
                (username,),
            )
            row = cursor.fetchone()
            if row is None:
                return False, "Invalid username or password.", None
            stored = row["password_hash"]
            if "$" not in stored:
                return False, "Corrupt credential record.", None
            salt, digest = stored.split("$", 1)
            if not secrets.compare_digest(_hash_password(password, salt), digest):
                return False, "Invalid username or password.", None
            return True, "Signed in successfully.", int(row["user_id"])
    except Exception as exc:
        logger.error("verify_user failed: %s", exc)
        return False, f"Sign-in failed: {exc}", None


def get_username(user_id: int) -> Optional[str]:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row["username"] if row else None
    except Exception as exc:
        logger.error("get_username failed: %s", exc)
        return None


def get_leaderboard(limit: int = 1000) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM screener_leaderboard ORDER BY composite_score DESC LIMIT ?",
                conn,
                params=(limit,),
            )
        return df
    except Exception as exc:
        logger.error("get_leaderboard failed: %s", exc)
        return pd.DataFrame()


def get_ticker_row(ticker: str) -> Optional[pd.Series]:
    ticker = (ticker or "").strip().upper()
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM screener_leaderboard WHERE ticker = ? LIMIT 1",
                conn,
                params=(ticker,),
            )
        if not df.empty:
            return df.iloc[0]
        market_mode = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
        if market_mode in {"mock", "offline", "test"}:
            for mock in MOCK_LEADERBOARD:
                if mock["ticker"] == ticker:
                    return pd.Series(mock)
        return None
    except Exception as exc:
        logger.error("get_ticker_row failed: %s", exc)
        return None


def get_leaderboard_last_updated() -> Optional[datetime]:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(last_updated) AS max_ts FROM screener_leaderboard")
            row = cursor.fetchone()
            if row is None or row["max_ts"] is None:
                return None
            return datetime.strptime(str(row["max_ts"]), "%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logger.error("get_leaderboard_last_updated failed: %s", exc)
        return None


def upsert_leaderboard_rows(rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return False
    stamp = _now_iso()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            _ensure_leaderboard_extra_columns(cursor)
            for row in rows:
                sources = row.get("fundamentals_sources") or []
                sources_n = int(row.get("sources_ok_count") or (len(sources) if sources else 0))
                verified = 1 if row.get("fundamentals_verified") else 0
                quality = str(row.get("data_quality") or ("VERIFIED" if verified else "UNVERIFIED"))
                ohlcv = 1 if row.get("ohlcv_ready") else 0
                price_source = str(row.get("price_source") or "")[:40] or None
                price_kind = str(row.get("price_kind") or "")[:20] or None
                prev_close = row.get("prev_close")
                try:
                    prev_close = float(prev_close) if prev_close is not None else None
                except (TypeError, ValueError):
                    prev_close = None
                cursor.execute(
                    """
                    INSERT INTO screener_leaderboard (
                        ticker, company_name, description, sector, industry,
                        composite_score, fundamental_score, technical_score,
                        close_price, atr_value, is_buyable, last_updated,
                        roic, net_debt_ebitda, peg_ratio, interest_coverage,
                        promoter_pledge_pct, yoy_profit_growth, sma_50, sma_200,
                        rsi_14, delivery_pct_10d, alpha_3m,
                        pe_ratio, roe, data_quality, fundamentals_verified, sources_ok_count,
                        ohlcv_ready, price_source, price_kind, prev_close
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        company_name=excluded.company_name,
                        description=excluded.description,
                        sector=excluded.sector,
                        industry=excluded.industry,
                        composite_score=excluded.composite_score,
                        fundamental_score=excluded.fundamental_score,
                        technical_score=excluded.technical_score,
                        close_price=excluded.close_price,
                        atr_value=excluded.atr_value,
                        is_buyable=excluded.is_buyable,
                        last_updated=excluded.last_updated,
                        roic=excluded.roic,
                        net_debt_ebitda=excluded.net_debt_ebitda,
                        peg_ratio=excluded.peg_ratio,
                        interest_coverage=excluded.interest_coverage,
                        promoter_pledge_pct=excluded.promoter_pledge_pct,
                        yoy_profit_growth=excluded.yoy_profit_growth,
                        sma_50=excluded.sma_50,
                        sma_200=excluded.sma_200,
                        rsi_14=excluded.rsi_14,
                        delivery_pct_10d=excluded.delivery_pct_10d,
                        alpha_3m=excluded.alpha_3m,
                        pe_ratio=excluded.pe_ratio,
                        roe=excluded.roe,
                        data_quality=excluded.data_quality,
                        fundamentals_verified=excluded.fundamentals_verified,
                        sources_ok_count=excluded.sources_ok_count,
                        ohlcv_ready=excluded.ohlcv_ready,
                        price_source=COALESCE(excluded.price_source, screener_leaderboard.price_source),
                        price_kind=COALESCE(excluded.price_kind, screener_leaderboard.price_kind),
                        prev_close=COALESCE(excluded.prev_close, screener_leaderboard.prev_close)
                    """,
                    (
                        row["ticker"], row.get("company_name", row["ticker"]),
                        row.get("description", ""), row.get("sector", ""), row.get("industry", ""),
                        row.get("composite_score", 0.0), row.get("fundamental_score", 0.0),
                        row.get("technical_score", 0.0), row.get("close_price", 0.0),
                        row.get("atr_value", 0.0), int(row.get("is_buyable", 0)), stamp,
                        row.get("roic"), row.get("net_debt_ebitda"),
                        row.get("peg_ratio"), row.get("interest_coverage"),
                        row.get("promoter_pledge_pct"), row.get("yoy_profit_growth"),
                        row.get("sma_50", 0.0), row.get("sma_200", 0.0),
                        row.get("rsi_14", 50.0), row.get("delivery_pct_10d", 0.0),
                        row.get("alpha_3m", 0.0),
                        row.get("pe_ratio"),
                        row.get("roe"),
                        quality,
                        verified,
                        sources_n,
                        ohlcv,
                        price_source,
                        price_kind,
                        prev_close,
                    ),
                )
        return True
    except Exception as exc:
        logger.error("upsert_leaderboard_rows failed: %s", exc)
        return False


def delete_tickers(tickers: List[str]) -> int:
    """Remove incomplete / rejected tickers from the screener board."""
    if not tickers:
        return 0
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            n = 0
            for t in tickers:
                cursor.execute(
                    "DELETE FROM screener_leaderboard WHERE ticker = ?",
                    (str(t).upper(),),
                )
                n += cursor.rowcount
            return n
    except Exception as exc:
        logger.error("delete_tickers failed: %s", exc)
        return 0


def purge_outside_universe(universe: List[str]) -> Dict[str, int]:
    """Delete leaderboard + load-attempt rows that are not in the active universe."""
    keep = {str(t).strip().upper() for t in universe if str(t).strip()}
    removed_board = 0
    removed_attempts = 0
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            board = [
                str(r["ticker"]).upper()
                for r in cursor.execute("SELECT ticker FROM screener_leaderboard").fetchall()
            ]
            outside = [t for t in board if t not in keep]
            for t in outside:
                cursor.execute("DELETE FROM screener_leaderboard WHERE ticker = ?", (t,))
                removed_board += cursor.rowcount
            _ensure_load_attempts_table(cursor)
            attempts = [
                str(r["ticker"]).upper()
                for r in cursor.execute("SELECT ticker FROM screener_load_attempts").fetchall()
            ]
            for t in attempts:
                if t not in keep:
                    cursor.execute("DELETE FROM screener_load_attempts WHERE ticker = ?", (t,))
                    removed_attempts += cursor.rowcount
        return {"board": removed_board, "attempts": removed_attempts, "kept": len(keep)}
    except Exception as exc:
        logger.error("purge_outside_universe failed: %s", exc)
        return {"board": 0, "attempts": 0, "kept": len(keep)}


def universe_leaderboard_count(universe: List[str]) -> int:
    """How many active-universe tickers are already on the board."""
    keep = {str(t).strip().upper() for t in universe if str(t).strip()}
    if not keep:
        return 0
    try:
        have = set(list_leaderboard_tickers())
        return len(have & keep)
    except Exception:
        return 0


def get_active_positions(user_id: int) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            return pd.read_sql_query(
                """
                SELECT position_id, user_id, ticker, entry_timestamp, entry_price,
                       stop_loss, target, quantity, current_price, unrealized_pnl
                FROM active_positions
                WHERE user_id = ?
                ORDER BY entry_timestamp DESC
                """,
                conn,
                params=(user_id,),
            )
    except Exception as exc:
        logger.error("get_active_positions failed: %s", exc)
        return pd.DataFrame()


def get_closed_trades(user_id: int, limit: int = 500) -> pd.DataFrame:
    try:
        with get_connection() as conn:
            return pd.read_sql_query(
                """
                SELECT id, user_id, ticker, entry_timestamp, exit_timestamp,
                       entry_price, exit_price, quantity, final_pnl,
                       exit_reason, exit_status
                FROM closed_trades_history
                WHERE user_id = ?
                ORDER BY exit_timestamp DESC
                LIMIT ?
                """,
                conn,
                params=(user_id, limit),
            )
    except Exception as exc:
        logger.error("get_closed_trades failed: %s", exc)
        return pd.DataFrame()


def open_signal(
    user_id: int,
    ticker: str,
    entry_price: float,
    stop_loss: float,
    target: float,
) -> Tuple[bool, str]:
    """Open a forward-test signal at fixed Quantity = 1. Capital is irrelevant."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT position_id FROM active_positions
                WHERE user_id = ? AND ticker = ?
                """,
                (user_id, ticker.upper()),
            )
            if cursor.fetchone() is not None:
                return False, f"{ticker.upper()} already has an active forward-test signal."

            stamp = _now_iso()
            cursor.execute(
                """
                INSERT INTO active_positions (
                    user_id, ticker, entry_timestamp, entry_price, stop_loss, target,
                    quantity, current_price, unrealized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0)
                """,
                (
                    user_id,
                    ticker.upper(),
                    stamp,
                    float(entry_price),
                    float(stop_loss),
                    float(target),
                    FIXED_QUANTITY,
                    float(entry_price),
                ),
            )
        return True, f"Tracked 1 share of {ticker.upper()} @ ₹{entry_price:.2f}."
    except Exception as exc:
        logger.error("open_signal failed: %s", exc)
        return False, str(exc)


def close_signal(
    user_id: int,
    position_id: int,
    exit_price: float,
    exit_status: str,
) -> Tuple[bool, str, float]:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT position_id, user_id, ticker, entry_timestamp, entry_price, quantity
                FROM active_positions
                WHERE position_id = ? AND user_id = ?
                """,
                (position_id, user_id),
            )
            pos = cursor.fetchone()
            if pos is None:
                return False, "Active signal not found.", 0.0

            entry_price = float(pos["entry_price"])
            quantity = int(pos["quantity"] or FIXED_QUANTITY)
            final_pnl = (float(exit_price) - entry_price) * quantity
            exit_ts = _now_iso()

            cursor.execute(
                """
                INSERT INTO closed_trades_history (
                    user_id, ticker, entry_timestamp, exit_timestamp,
                    entry_price, exit_price, quantity, final_pnl,
                    exit_reason, exit_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    pos["ticker"],
                    pos["entry_timestamp"],
                    exit_ts,
                    entry_price,
                    float(exit_price),
                    quantity,
                    final_pnl,
                    exit_status,
                    exit_status,
                ),
            )
            cursor.execute(
                "DELETE FROM active_positions WHERE position_id = ? AND user_id = ?",
                (position_id, user_id),
            )
        return True, f"Closed {pos['ticker']} — {exit_status}. Δ ₹{final_pnl:,.2f}.", final_pnl
    except Exception as exc:
        logger.error("close_signal failed: %s", exc)
        return False, str(exc), 0.0


def update_position_mark(position_id: int, current_price: float, unrealized_pnl: float) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE active_positions
                SET current_price = ?, unrealized_pnl = ?
                WHERE position_id = ?
                """,
                (current_price, unrealized_pnl, position_id),
            )
        return True
    except Exception as exc:
        logger.error("update_position_mark failed: %s", exc)
        return False


init_database()
