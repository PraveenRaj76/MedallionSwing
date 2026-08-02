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
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

DATABASE_PATH = os.environ.get(
    "MEDALLION_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "medallion_system.db"),
)
_DB_LOCK = threading.RLock()
FIXED_QUANTITY = 1

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
                    alpha_3m REAL
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
    for row in MOCK_LEADERBOARD:
        cursor.execute(
            """
            INSERT OR IGNORE INTO screener_leaderboard (
                ticker, company_name, description, sector, industry,
                composite_score, fundamental_score, technical_score,
                close_price, atr_value, is_buyable, last_updated,
                roic, net_debt_ebitda, peg_ratio, interest_coverage,
                promoter_pledge_pct, yoy_profit_growth, sma_50, sma_200,
                rsi_14, delivery_pct_10d, alpha_3m
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["ticker"], row["company_name"], row["description"], row["sector"], row["industry"],
                row["composite_score"], row["fundamental_score"], row["technical_score"],
                row["close_price"], row["atr_value"], row["is_buyable"], stamp,
                row["roic"], row["net_debt_ebitda"], row["peg_ratio"], row["interest_coverage"],
                row["promoter_pledge_pct"], row["yoy_profit_growth"], row["sma_50"], row["sma_200"],
                row["rsi_14"], row["delivery_pct_10d"], row["alpha_3m"],
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


def ensure_mock_leaderboard() -> None:
    """Emergency / offline seed only — not used as primary market source in live mode."""
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


def get_leaderboard(limit: int = 50) -> pd.DataFrame:
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
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO screener_leaderboard (
                        ticker, company_name, description, sector, industry,
                        composite_score, fundamental_score, technical_score,
                        close_price, atr_value, is_buyable, last_updated,
                        roic, net_debt_ebitda, peg_ratio, interest_coverage,
                        promoter_pledge_pct, yoy_profit_growth, sma_50, sma_200,
                        rsi_14, delivery_pct_10d, alpha_3m
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        alpha_3m=excluded.alpha_3m
                    """,
                    (
                        row["ticker"], row.get("company_name", row["ticker"]),
                        row.get("description", ""), row.get("sector", ""), row.get("industry", ""),
                        row.get("composite_score", 0.0), row.get("fundamental_score", 0.0),
                        row.get("technical_score", 0.0), row.get("close_price", 0.0),
                        row.get("atr_value", 0.0), int(row.get("is_buyable", 0)), stamp,
                        row.get("roic", 0.0), row.get("net_debt_ebitda", 0.0),
                        row.get("peg_ratio", 0.0), row.get("interest_coverage", 0.0),
                        row.get("promoter_pledge_pct", 0.0), row.get("yoy_profit_growth", 0.0),
                        row.get("sma_50", 0.0), row.get("sma_200", 0.0),
                        row.get("rsi_14", 50.0), row.get("delivery_pct_10d", 0.0),
                        row.get("alpha_3m", 0.0),
                    ),
                )
        return True
    except Exception as exc:
        logger.error("upsert_leaderboard_rows failed: %s", exc)
        return False


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
