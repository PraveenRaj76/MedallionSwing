# Local / Cloud live Nifty 500 — no mock data

## Preferred (in-app, works on Streamlit Cloud)

1. Run the app: `streamlit run app.py`
2. Sign in → Screener
3. Leave **Auto-fill full universe** on — progress bar fills toward the full list
4. Leave **Auto-fill fundamentals** on after prices land (multi-source verify)

Optional fast kick: **Load live bootstrap (12 names)** then let auto-hydrate finish the rest.

## Optional CLI (local PC only, still supported)

```powershell
.\venv\Scripts\python.exe sync_nifty500_local.py --clear
# Optional fundamentals (slow):
.\venv\Scripts\python.exe sync_nifty500_local.py --with-fundamentals
.\venv\Scripts\streamlit.exe run app.py
```

## Env

```powershell
$env:MEDALLION_MARKET_MODE="live"
$env:MEDALLION_SSL_VERIFY="0"   # corporate SSL intercept
```
