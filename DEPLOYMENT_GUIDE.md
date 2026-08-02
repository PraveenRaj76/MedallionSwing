# 🚀 Medallion Swing Engine - Production Hosting & Deployment Guide

**Complete infrastructure blueprint for free, serverless hosting of the Nifty 500 swing trading platform**

---

## 📋 Table of Contents

1. [Quick Start (Local Testing)](#quick-start-local-testing)
2. [Phase 1: Deploy to Streamlit Cloud (Free)](#phase-1-deploy-to-streamlit-cloud-free)
3. [Phase 2: Cloud Database Setup (Neon Postgres)](#phase-2-cloud-database-setup-neon-postgres)
4. [Phase 3: Automated Data Fetching (Render Cron)](#phase-3-automated-data-fetching-render-cron)
5. [Phase 4: Production Monitoring & Maintenance](#phase-4-production-monitoring--maintenance)

---

## Quick Start (Local Testing)

### Prerequisites
```bash
pip install streamlit pandas sqlite3 numpy plotly
```

### Run Locally
```bash
streamlit run medallion_swing_unified.py
```

The app will:
- Initialize `medallion_system.db` automatically
- Populate with mock data (TCS, RELIANCE, INFY, HDFCBANK)
- Open on `http://localhost:8501`

---

## Phase 1: Deploy to Streamlit Cloud (Free)

### Step 1: Prepare Repository Structure

Create GitHub repo with this structure:
```
medallion-swing/
├── medallion_swing_unified.py
├── requirements.txt
├── .gitignore
├── README.md
├── config.toml
└── medals.db (NOT committed; created at runtime)
```

### Step 2: Create requirements.txt

```txt
streamlit==1.28.1
pandas==2.0.3
plotly==5.17.0
numpy==1.24.3
```

### Step 3: Create .streamlit/config.toml

```toml
[theme]
primaryColor = "#6366f1"
backgroundColor = "#f5f7fa"
secondaryBackgroundColor = "#e4e8f0"
textColor = "#1a1f3a"
font = "sans serif"

[logger]
level = "info"

[client]
showErrorDetails = false
toolbarMode = "viewer"

[server]
port = 8501
headless = true
runOnSave = true
maxUploadSize = 200
```

### Step 4: Create .gitignore

```
medallion_system.db
*.pyc
__pycache__/
.env
.streamlit/secrets.toml
.DS_Store
```

### Step 5: Deploy via Streamlit Cloud

1. Go to [streamlit.io/cloud](https://streamlit.io/cloud)
2. Sign up with GitHub
3. Click "New App" → Select your repo
4. Configure:
   - **Repository:** `username/medallion-swing`
   - **Branch:** `main`
   - **Main file path:** `medallion_swing_unified.py`

5. Click "Deploy"
6. App goes live at: `https://medallion-swing.streamlit.app`

### ⚠️ Important: Streamlit Cloud Limitations

**Database Persistence Issue:**
- Streamlit Cloud runs on ephemeral filesystem
- `medallion_system.db` resets on app restart (free tier resets daily)
- Paper trading ledger data is LOST

**Solution: Migrate to Cloud Database (Phase 2)**

---

## Phase 2: Cloud Database Setup (Neon Postgres)

### Why Neon Postgres?
- ✅ Free tier (5GB storage, unlimited projects)
- ✅ Serverless (auto-scales to zero)
- ✅ No database server to manage
- ✅ Persistent data across app restarts
- ✅ PostgreSQL-compatible

### Step 1: Create Neon Account

1. Go to [neon.tech](https://neon.tech)
2. Sign up (free account = 5GB storage)
3. Create new project: "Medallion Swing"
4. Get connection string (looks like):
```
postgresql://user:password@ep-xyz-123.us-east-2.neon.tech/medallion_swing?sslmode=require
```

### Step 2: Update medallion_swing_unified.py for Postgres

Replace SQLite code section with PostgreSQL:

```python
import psycopg2
from psycopg2 import sql
import os

# Get connection string from Streamlit secrets
DATABASE_URL = st.secrets.get("DATABASE_URL", "")

def get_db_connection():
    """Get PostgreSQL connection."""
    return psycopg2.connect(DATABASE_URL)

def init_database():
    """Initialize PostgreSQL tables."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Screener Leaderboard Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS screener_leaderboard (
                ticker VARCHAR(10) PRIMARY KEY,
                company_name VARCHAR(255) NOT NULL,
                sector VARCHAR(100),
                fundamental_score FLOAT,
                technical_score FLOAT,
                composite_score FLOAT,
                cmp FLOAT,
                atr_14 FLOAT,
                roic FLOAT,
                net_debt_ebitda FLOAT,
                peg_ratio FLOAT,
                interest_coverage FLOAT,
                promoter_pledge_pct FLOAT,
                yoy_profit_growth FLOAT,
                price_200sma FLOAT,
                sma_50 FLOAT,
                sma_200 FLOAT,
                rsi_14 FLOAT,
                delivery_pct_10d FLOAT,
                alpha_3m FLOAT,
                description TEXT,
                industry VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Active Positions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                position_id SERIAL PRIMARY KEY,
                ticker VARCHAR(10) NOT NULL REFERENCES screener_leaderboard(ticker),
                action VARCHAR(10) NOT NULL,
                entry_price FLOAT NOT NULL,
                current_price FLOAT,
                stop_loss FLOAT NOT NULL,
                target FLOAT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(20) DEFAULT 'OPEN',
                unrealized_pnl FLOAT
            )
        """)

        # Closed Trades Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades_history (
                trade_id SERIAL PRIMARY KEY,
                ticker VARCHAR(10) NOT NULL REFERENCES screener_leaderboard(ticker),
                action VARCHAR(10) NOT NULL,
                entry_price FLOAT NOT NULL,
                exit_price FLOAT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_date TIMESTAMP,
                exit_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                realized_pnl FLOAT,
                exit_reason VARCHAR(100)
            )
        """)

        # Portfolio Ledger Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_ledger (
                ledger_id SERIAL PRIMARY KEY,
                account_equity FLOAT DEFAULT 100000.0,
                available_cash FLOAT DEFAULT 100000.0,
                invested_capital FLOAT DEFAULT 0.0,
                total_portfolio_value FLOAT DEFAULT 100000.0,
                unrealized_pnl FLOAT DEFAULT 0.0,
                realized_pnl FLOAT DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Price History Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                history_id SERIAL PRIMARY KEY,
                ticker VARCHAR(10) NOT NULL REFERENCES screener_leaderboard(ticker),
                date DATE,
                open FLOAT,
                high FLOAT,
                low FLOAT,
                close FLOAT,
                volume INTEGER
            )
        """)

        # Initialize portfolio ledger
        cursor.execute("SELECT COUNT(*) FROM portfolio_ledger")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO portfolio_ledger
                (account_equity, available_cash, invested_capital, total_portfolio_value)
                VALUES (%s, %s, %s, %s)
            """, (100000.0, 100000.0, 0.0, 100000.0))

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("PostgreSQL database initialized")
        return True

    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        return False
```

### Step 3: Configure Streamlit Secrets

Create `.streamlit/secrets.toml` (local only, DO NOT COMMIT):

```toml
DATABASE_URL = "postgresql://user:password@ep-xyz.neon.tech/medallion_swing?sslmode=require"
```

**For Streamlit Cloud deployment:**
1. Go to app settings → Secrets
2. Paste the DATABASE_URL
3. Deploy

### Step 4: Add psycopg2 to requirements.txt

```txt
streamlit==1.28.1
pandas==2.0.3
plotly==5.17.0
numpy==1.24.3
psycopg2-binary==2.9.7
```

---

## Phase 3: Automated Data Fetching (Render Cron)

### Problem

The app needs **fresh market data hourly during market hours** (9:15 AM - 3:30 PM IST) instead of using stale mock data.

### Solution: Render Background Jobs + Python Script

### Step 1: Create Data Fetch Script

Create `fetch_market_data.py`:

```python
"""
Hourly market data fetcher for Medallion Swing
Runs on Render background job (free tier: 100 hours/month)
"""

import psycopg2
import requests
import os
from datetime import datetime, time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
NIFTY_500_TICKERS = [
    'TCS', 'RELIANCE', 'INFY', 'HDFCBANK', 'HDFC', 'WIPRO', 'AXISBANK', 
    'MARUTI', 'BAJAJFINSV', 'KOTAKBANK', 'SBILIFE', 'ASIANPAINT', 'BHARTIARTL',
    'SUNPHARMA', 'TECHM', 'ICICIBANK', 'LICI', 'BAJFINANCE', 'HINDUNILVR',
    'ADANIENT', 'NTPC', 'POWERGRID', 'ONGC', 'JSWSTEEL', 'COALINDIA'
]

def get_db_connection():
    """Connect to Neon Postgres."""
    return psycopg2.connect(DATABASE_URL)

def fetch_live_data(ticker: str) -> dict:
    """
    Fetch live market data from NSE/BSE via free API.
    Using finnhub.io free tier (15/minute quota)
    """
    try:
        # Alternative: Use yfinance for free data
        import yfinance as yf

        data = yf.Ticker(ticker + ".NS")  # .NS = NSE (India)
        history = data.history(period="1y")

        if history.empty:
            return None

        latest = history.iloc[-1]
        sma_50 = history['Close'].rolling(50).mean().iloc[-1]
        sma_200 = history['Close'].rolling(200).mean().iloc[-1]

        # Calculate RSI
        delta = history['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))

        return {
            'ticker': ticker,
            'cmp': float(latest['Close']),
            'high': float(latest['High']),
            'low': float(latest['Low']),
            'volume': int(latest['Volume']),
            'sma_50': float(sma_50),
            'sma_200': float(sma_200),
            'rsi_14': float(rsi),
            'atr_14': calculate_atr(history)
        }

    except Exception as e:
        logger.error(f"Data fetch error for {ticker}: {e}")
        return None

def calculate_atr(history, period=14):
    """Calculate Average True Range."""
    high_low = history['High'] - history['Low']
    high_close = abs(history['High'] - history['Close'].shift())
    low_close = abs(history['Low'] - history['Close'].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(period).mean()

    return float(atr.iloc[-1]) if not atr.empty else 1.0

def update_market_data():
    """Update all tickers in database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    updated_count = 0

    for ticker in NIFTY_500_TICKERS:
        data = fetch_live_data(ticker)

        if data:
            cursor.execute("""
                UPDATE screener_leaderboard
                SET cmp = %s, atr_14 = %s, sma_50 = %s, sma_200 = %s, rsi_14 = %s
                WHERE ticker = %s
            """, (data['cmp'], data['atr_14'], data['sma_50'], data['sma_200'], 
                  data['rsi_14'], ticker))

            # Insert into price history
            cursor.execute("""
                INSERT INTO price_history (ticker, date, open, high, low, close, volume)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (ticker, data.get('open', data['cmp']), data['high'], 
                  data['low'], data['cmp'], data['volume']))

            updated_count += 1
            logger.info(f"Updated {ticker}: ₹{data['cmp']:.2f} | RSI: {data['rsi_14']:.1f}")

    conn.commit()
    cursor.close()
    conn.close()

    logger.info(f"Market data update complete: {updated_count} tickers")

def is_market_hours():
    """Check if it's market hours (9:15 AM - 3:30 PM IST)."""
    ist = datetime.now()  # Assume server in IST or convert
    market_open = time(9, 15)
    market_close = time(15, 30)
    return market_open <= ist.time() <= market_close and ist.weekday() < 4  # Mon-Fri

if __name__ == "__main__":
    logger.info(f"Data fetch started at {datetime.now()}")

    if is_market_hours():
        update_market_data()
        logger.info("✅ Market data updated successfully")
    else:
        logger.info("⏸️ Outside market hours; skipping update")
```

Update `requirements.txt`:
```txt
streamlit==1.28.1
pandas==2.0.3
plotly==5.17.0
numpy==1.24.3
psycopg2-binary==2.9.7
yfinance==0.2.28
requests==2.31.0
```

### Step 2: Deploy to Render

1. Go to [render.com](https://render.com)
2. Sign up (free account)
3. Create → Cron Job
4. Configure:
   - **Name:** `medallion-data-fetcher`
   - **Repository:** Your GitHub repo
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python fetch_market_data.py`
   - **Schedule:** `0 9,10,11,12,13,14,15 * * 1-5` (9 AM - 3 PM, Mon-Fri IST)
   - **Environment:** Add `DATABASE_URL` secret

5. Deploy

### Step 3: Monitor Cron Job

```
Render Dashboard → medallion-data-fetcher → Logs
```

Check logs for `✅ Market data updated successfully`

---

## Phase 4: Production Monitoring & Maintenance

### Uptime Monitoring

Free uptime monitor via [UptimeRobot](https://uptimerobot.com):
1. Add monitor: `https://medallion-swing.streamlit.app`
2. Check interval: 5 minutes
3. Get alerts if app goes down

### Database Backups

Neon Postgres includes:
- ✅ Automatic daily backups (retention: 7 days)
- ✅ Point-in-time recovery available

Manual backup:
```bash
pg_dump DATABASE_URL > backup_$(date +%Y%m%d).sql
```

### Performance Optimization

**Streamlit Cloud Free Tier Limits:**
- Memory: 1 GB
- CPU: 1 core
- Connections: 1 concurrent

**Optimizations:**
```python
# Use caching to reduce database calls
@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_leaderboard_from_db():
    # Database query here
    pass

# Use columns for layout efficiency
col1, col2 = st.columns(2)
with col1:
    st.metric("Portfolio Value", "₹100,000")
with col2:
    st.metric("Available Cash", "₹50,000")
```

### Scaling Beyond Free Tier

**When you need more capacity:**

| Tier | Cost | Features |
|------|------|----------|
| Streamlit Cloud (Free) | $0 | 1GB RAM, 1 CPU, 1 concurrent |
| Streamlit Cloud (Pro) | $30/mo | 3GB RAM, 2 CPU, 3 concurrent |
| **Recommended:** Railway.app | $5-20/mo | Better performance, more control |
| EC2 + RDS | $20-50/mo | Full control, best performance |

**Recommendation:** Use free tier for prototype, upgrade to Railway.app ($5/mo) for production with consistent traffic.

---

## Complete Deployment Checklist

- [ ] **Local Setup**
  - [ ] Python 3.10+
  - [ ] Virtual environment
  - [ ] All packages installed
  - [ ] App runs locally without errors

- [ ] **Phase 1: Streamlit Cloud**
  - [ ] GitHub repo created
  - [ ] `.gitignore` excludes `.db`
  - [ ] `requirements.txt` updated
  - [ ] App deployed to Streamlit Cloud
  - [ ] Mock data works

- [ ] **Phase 2: Neon Postgres**
  - [ ] Neon account created
  - [ ] Database created
  - [ ] Connection string obtained
  - [ ] `secrets.toml` configured
  - [ ] Postgres migration code added
  - [ ] Secrets added to Streamlit Cloud
  - [ ] App redeployed
  - [ ] Database persistence verified

- [ ] **Phase 3: Render Cron**
  - [ ] `fetch_market_data.py` created
  - [ ] Render account created
  - [ ] Cron job configured
  - [ ] Schedule set to market hours
  - [ ] Environment variables added
  - [ ] First run successful

- [ ] **Phase 4: Monitoring**
  - [ ] UptimeRobot configured
  - [ ] Neon backups verified
  - [ ] Logs monitored
  - [ ] Performance optimized

---

## Quick Reference: Environment Variables

**For Streamlit Cloud & Render:**

```
DATABASE_URL=postgresql://user:pass@ep-xyz.neon.tech/medallion_swing?sslmode=require
ENVIRONMENT=production
LOG_LEVEL=info
```

**For Local Development:**

`.env` file (never commit):
```
DATABASE_URL=sqlite:///medallion_system.db
ENVIRONMENT=development
LOG_LEVEL=debug
```

---

## Troubleshooting

### Issue: "Database connection refused"
**Solution:** Check `DATABASE_URL` in Streamlit secrets matches Neon connection string

### Issue: "Cron job not running"
**Solution:** Check if `is_market_hours()` function returns True; verify schedule in Render

### Issue: "App crashes after restart"
**Solution:** Migrate from SQLite to Neon Postgres (Phase 2)

### Issue: "Slow performance"
**Solution:** 
- Enable query caching with `@st.cache_data`
- Upgrade to Streamlit Pro or Railway.app
- Optimize database indices

---

## Cost Summary

| Service | Cost | Purpose |
|---------|------|---------|
| Streamlit Cloud | Free | App hosting |
| Neon Postgres | Free (5GB) | Database |
| Render Cron | Free (100h/mo) | Data updates |
| **Total** | **$0** | **Full production platform** |

---

## Support & Resources

- Streamlit Docs: https://docs.streamlit.io
- Neon Docs: https://neon.tech/docs
- Render Docs: https://render.com/docs
- Plotly Docs: https://plotly.com/python/
- PostgreSQL Docs: https://www.postgresql.org/docs/

---

**Generated:** 2026-07-30  
**Version:** Medallion Swing v1.0  
**Last Updated:** Production-Ready
