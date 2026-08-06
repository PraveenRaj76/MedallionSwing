# Live market data — LTP first

## Critical field rule (Groww)
Groww JSON has both:
- `ltp` → **true live price** (use this)
- `close` → **previous close** (do NOT use as CMP)

Moneycontrol `pricecurrent` and Yahoo `regularMarketPrice` are also live LTP.

## Free sources (no API key)
1. Groww NSE CASH `ltp`
2. Moneycontrol `pricecurrent`
3. Yahoo `regularMarketPrice`

**No free official NSE/BSE retail API key exists.**

## Refresh
Screener → **Refresh** pulls latest LTP for `data/nse_universe.txt` (Midcap 150 + Smallcap 50 ≈ 200).
Table columns: **Live CMP** + **Updated** time.

## Start
```powershell
$env:MEDALLION_MARKET_MODE="live"
$env:MEDALLION_SSL_VERIFY="0"
.\venv\Scripts\python.exe -m streamlit run app.py --server.address localhost
```
Open http://localhost:8501
