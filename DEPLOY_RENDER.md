# Medallion Swing — Free Deploy Guide (Render) + How to Use

This guide gets **Medallion Swing** live on Render’s free tier and walks through every screen after deploy.

---

## E2E regression status (pre-prod)

Latest local suite (`e2e_regression.py`):

```text
E2E REGRESSION SUMMARY: 56/56 PASSED | 0 FAILED
```

Covered: templates/CSS, auth register/login/reject, schema (no capital ledger), Screener buy (qty=1), Search buy, RSI lock, chart build, sync + SUCCESSFUL/BAD TRADE clearance, scorecard metrics, multi-user isolation, all nav/buttons/contracts, logout helpers, Streamlit AppTest cold start.

Re-run anytime:

```powershell
cd "C:\Users\prdhan\OneDrive - ASSA ABLOY Group\Desktop\Medallion Swing\medallion-swing"
.\venv\Scripts\python.exe e2e_regression.py
```

---

## Streamlit Community Cloud (recommended free, no credit card)

**Full production mode (not partial):** the app now hydrates the **full Nifty universe in-app** (progressive batches + auto fundamentals). No CLI on the server. Price path is Yahoo chart → yfinance → Tickertape/Moneycontrol/Screener CMP.

**Deploy failed on Python 3.14?** Cloud ignores `runtime.txt`. You must set Python in the UI.

1. Push latest code + `requirements.txt` to GitHub  
2. In [share.streamlit.io](https://share.streamlit.io) → your app → **Settings** (or delete & redeploy)  
3. **Advanced settings → Python version → 3.12** (or 3.11) — **not 3.14**  
4. Main file: `app.py` · Reboot / Redeploy  
5. Secrets (recommended — paste from `.streamlit/secrets.toml.example`):

```toml
MEDALLION_MARKET_MODE = "live"
MEDALLION_SSL_VERIFY = "1"
MEDALLION_HYDRATE_BATCH = "12"
MEDALLION_FUND_BATCH = "4"
```

6. After login: leave **Auto-fill full universe** on. Screener progress bar fills toward Nifty 500 while you use Search / Forward-Test.  
7. Optional cold-start boost: set `MEDALLION_SEED_DB_URL` to a hosted copy of your local `medallion_system.db` so redeploys/sleep wake with data already loaded.

---

## Part A — Deploy freely on Render (step by step)

### What you need

1. A free [GitHub](https://github.com) account  
2. A free [Render](https://render.com) account (sign up with GitHub)  
3. This project pushed to a GitHub repository  

### Step 1 — Put the project on GitHub

In PowerShell (from the project folder):

```powershell
cd "C:\Users\prdhan\OneDrive - ASSA ABLOY Group\Desktop\Medallion Swing\medallion-swing"

# If git is not initialized yet:
git init
git add app.py database_engine.py data_pipeline.py nse_data_provider.py data requirements.txt Procfile render.yaml runtime.txt e2e_regression.py templates .streamlit .gitignore DEPLOY_RENDER.md
git commit -m "Medallion Swing live NSE forward-test engine ready for Render"

# Create a repo on GitHub (via website), then:
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/medallion-swing.git
git push -u origin main
```

Do **not** commit `venv/` or large local DBs if avoidable (already ignored in `.gitignore`).

### Step 2 — Create a Web Service on Render

1. Open [https://dashboard.render.com](https://dashboard.render.com)  
2. Click **New +** → **Web Service**  
3. Connect your GitHub account and select **`medallion-swing`**  
4. Configure:

| Field | Value |
|--------|--------|
| Name | `medallion-swing` |
| Region | Closest to you (e.g. Singapore / Frankfurt) |
| Runtime | **Python 3** |
| Branch | `main` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false` |
| Instance type | **Free** |

5. Click **Advanced** → add environment variables (optional but recommended):

| Key | Value |
|-----|--------|
| `PYTHON_VERSION` | `3.11.9` |
| `MEDALLION_DB_PATH` | `/opt/render/project/src/medallion_system.db` |
| `MEDALLION_MARKET_MODE` | `live` |
| `MEDALLION_SSL_VERIFY` | `1` |

6. Click **Create Web Service**  

Render builds for a few minutes. When status is **Live**, open the URL like:

`https://medallion-swing.onrender.com`

### Step 3 — Alternative: Blueprint deploy

If you prefer YAML:

1. **New +** → **Blueprint**  
2. Select the repo containing `render.yaml`  
3. Apply → Render creates the free web service from the file  

### Step 4 — Free-tier realities (important)

| Topic | What happens on Free Render |
|--------|------------------------------|
| Cost | **$0** for the free web service |
| Sleep | App **spins down** after ~15 min idle; first open can take **30–60+ seconds** |
| SQLite | Disk is **ephemeral** — redeploys / restarts can **wipe** `medallion_system.db` |
| Persistence | For lasting data, later add a **paid Persistent Disk** or move to Postgres |

For demos and personal forward-testing, free SQLite is fine. Expect to re-create accounts after a wipe.

### Step 5 — Verify production health

1. Open your Render URL  
2. You should see the **login / create account** gate (no sidebar)  
3. Create an account → top nav appears (Screener / Search Profile / Forward-Test)  
4. On Render **Logs**, confirm no crash loops  

If build fails on Python version, set `PYTHON_VERSION=3.11.9` explicitly in Environment.

---

## Part B — How to use the live app (end-to-end user flow)

### 1. Create Account / Sign In

- **Create Account**: username ≥ 3 chars, password ≥ 6 chars  
- No funding needed — engine tracks **exactly 1 share** per signal  
- After login, background sync runs (`validate_active_signals`)

### 2. Screener (Home)

- Borderless HTML leaderboard of **live NSE** universe (Yahoo OHLC + Screener.in fundamentals)  
- Pick a ticker in the dropdown → factor report card opens  
- If **BUY** (above 200 SMA and RSI ≤ 65): click **EXECUTE ALGORITHMIC BUY**  
- Opens **1 share** with Stop = CMP − 2.5×ATR, Target = CMP + 6.0×ATR  
- Screen refreshes (`st.rerun`)  

### 3. Search Profile

- Type a ticker (e.g. `TCS`, `INFY`, `HDFCBANK`)  
- RSI **> 65** → overextended warning, buy locked  
- Otherwise same **EXECUTE ALGORITHMIC BUY** (qty = 1)  
- Technical chart (price, 200 SMA, volume, RSI)  

### 4. Forward-Test

- **Scorecard**: Total Signals · Win Rate % · Total Realized ₹ P&L  
- **Active Signals Monitor**: open 1-share legs  
- **Closed Signal Results**: SUCCESSFUL TRADE (green) / BAD TRADE (red), Δ ₹, % return, “Achieved in N Days”  
- **Refresh Quotes & Validate Signals**: force mark-to-market + auto exits  

### 5. Auto exits (always on login / nav / refresh)

- Close ≥ Target → archived **SUCCESSFUL TRADE**, removed from active  
- Close ≤ Stop → archived **BAD TRADE**, removed from active  

### 6. Log Out

- Top-right **Log Out** clears session → back to login gate  

### 7. Multi-user

- Each account only sees **its own** active/closed signals (filtered by `user_id`)  

---

## Part C — Local run (before / after deploy)

```powershell
cd "C:\Users\prdhan\OneDrive - ASSA ABLOY Group\Desktop\Medallion Swing\medallion-swing"
.\venv\Scripts\Activate.ps1
streamlit run app.py
```

Or without activate:

```powershell
.\venv\Scripts\streamlit.exe run app.py
```

Open `http://localhost:8501` (or the port shown in the terminal).

---

## Part D — Files Render uses

| File | Purpose |
|------|---------|
| `app.py` | Streamlit entrypoint |
| `requirements.txt` | pip dependencies |
| `Procfile` | Process start command |
| `runtime.txt` / `PYTHON_VERSION` | Python 3.11 |
| `render.yaml` | Optional Blueprint |
| `.streamlit/config.toml` | Headless + light theme |
| `e2e_regression.py` | Pre-prod regression (56 checks) |

---

## Data mode (live NSE)

| Setting | Meaning |
|---------|---------|
| `MEDALLION_MARKET_MODE=live` | **Default / production** — Yahoo NSE OHLC + Screener.in fundamentals + live charts |
| `MEDALLION_MARKET_MODE=mock` | Offline tests only |

**Refresh cadence (live):**
- Quotes / technicals refresh when a user logs in, navigates, or clicks **Refresh**, at most every **15 minutes**
- Fundamentals re-scrape at most every **24 hours** (otherwise reused from SQLite)
- Charts always pull live daily OHLC for the selected ticker

First login after deploy can take **1–3 minutes** while the Nifty-50 universe loads.

---

## Part E — Troubleshooting

| Symptom | Fix |
|---------|-----|
| Build fails `No module named streamlit` | Confirm Build Command installs `requirements.txt` |
| App crashes on boot | Check Render Logs; ensure Start Command uses `$PORT` |
| Site sleeps / slow first load | Normal on free tier — wait and refresh |
| Data disappeared after redeploy | Ephemeral disk — expected on free; recreate accounts or add Persistent Disk |
| `streamlit` not found locally | Use `.\venv\Scripts\streamlit.exe run app.py` |

---

## Checklist before calling it “prod”

- [x] E2E `56/56` local pass  
- [ ] Repo pushed to GitHub  
- [ ] Render free web service Live  
- [ ] Manual click-through: Create Account → Screener buy → Search buy → Forward-Test scorecard → Log Out  
- [ ] Second account cannot see first account’s signals  

You are clear to deploy on Render free and use the forward-test engine with zero capital setup.
