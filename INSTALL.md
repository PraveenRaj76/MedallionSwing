# 🚀 Medallion Swing Engine - Installation Guide

**Quick setup in 3 minutes!**

---

## 📋 Prerequisites

- **Python 3.8+** (download from python.org)
- **pip** (included with Python)
- **Git** (optional, for version control)

---

## ⚡ Installation (Windows/Mac/Linux)

### Step 1: Extract the ZIP File
```bash
unzip medallion-swing.zip
cd medallion-swing
```

### Step 2: Create Virtual Environment (Optional but Recommended)

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

This installs:
- streamlit (web framework)
- pandas (data processing)
- plotly (interactive charts)
- numpy (numerical computing)

### Step 4: Run the Application
```bash
streamlit run medallion_swing_unified.py
```

### Step 5: Open in Browser
Automatically opens at: **http://localhost:8501**

---

## ✅ Verify Installation

You should see:
1. Sidebar with "🪐 Medallion Swing Engine"
2. Navigation buttons: "Dashboard Leaderboard", "Advanced Search Portal", "Paper Trading Terminal"
3. No error messages in terminal

---

## 🎮 Quick Test

1. **Page 1 (Dashboard):**
   - See leaderboard with TCS, RELIANCE, INFY, HDFCBANK
   - Click any row to expand analysis

2. **Page 2 (Search):**
   - Search "TCS"
   - See price, stop loss, target calculations

3. **Page 3 (Trading):**
   - Deposit ₹100,000
   - Execute sample BUY order
   - Monitor position in real-time

---

## 🐛 Troubleshooting

### Issue: "Python not found"
**Solution:** 
- Check Python installation: `python --version`
- Use `python3` instead of `python` on Mac/Linux

### Issue: "streamlit not found"
**Solution:**
```bash
pip install --upgrade streamlit
```

### Issue: "Port 8501 already in use"
**Solution:**
```bash
streamlit run medallion_swing_unified.py --logger.level=debug --server.port=8502
```

### Issue: "No module named 'plotly'"
**Solution:**
```bash
pip install --upgrade plotly
```

### Issue: App crashes on startup
**Solution:**
1. Delete `medallion_system.db` (database file)
2. Restart: `streamlit run medallion_swing_unified.py`
3. App will auto-create fresh database

---

## 📚 Documentation

After installation, read these in order:

1. **README.md** (5 min) - Overview & quick reference
2. **QUICK_START.md** (15 min) - Detailed usage guide
3. **PROJECT_SUMMARY.md** (30 min) - Technical deep-dive
4. **DEPLOYMENT_GUIDE.md** (1 hour) - Deploy to cloud

---

## 🎯 What's Next?

### For Beginners:
1. Run app locally
2. Explore all 3 pages
3. Execute sample trades
4. Read QUICK_START.md

### For Developers:
1. Read PROJECT_SUMMARY.md
2. Review code structure
3. Explore database schema
4. Customize as needed

### For Production:
1. Read DEPLOYMENT_GUIDE.md
2. Set up Streamlit Cloud account
3. Deploy to free cloud hosting
4. Migrate to Neon Postgres

---

## 📦 Project Structure

```
medallion-swing/
├── medallion_swing_unified.py  ← Main app (2000+ lines)
├── requirements.txt             ← Python dependencies
├── .gitignore                   ← Git configuration
├── .streamlit/
│   └── config.toml             ← Streamlit settings
├── README.md                    ← Quick reference
├── INSTALL.md                   ← This file
├── QUICK_START.md              ← User guide
├── PROJECT_SUMMARY.md          ← Technical reference
├── DEPLOYMENT_GUIDE.md         ← Cloud hosting guide
└── medallion_system.db         ← Auto-created database
```

---

## 💾 Database

The app auto-creates `medallion_system.db` on first run. This SQLite database stores:
- Nifty 500 stocks (with mock data: TCS, RELIANCE, INFY, HDFCBANK)
- Your paper trading positions
- Portfolio ledger (cash balance, investments)
- Trade history

**No setup needed!** Database initializes automatically.

---

## 🌐 Deployment (Optional)

After testing locally, deploy for free:

1. **Streamlit Cloud** (free tier)
   - See DEPLOYMENT_GUIDE.md Phase 1
   - 1GB RAM, 1 CPU, no credit card needed

2. **Neon Postgres** (free tier)
   - See DEPLOYMENT_GUIDE.md Phase 2
   - 5GB database, auto-backups

3. **Render Cron** (free tier)
   - See DEPLOYMENT_GUIDE.md Phase 3
   - 100 hours/month, hourly market updates

**Total cost: $0/month (forever)**

---

## 🔧 System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.8 | 3.10+ |
| RAM | 512 MB | 2 GB |
| Disk | 100 MB | 500 MB |
| Internet | Optional | For cloud deployment |

---

## ✨ Key Files

| File | Purpose | Size |
|------|---------|------|
| medallion_swing_unified.py | Main application | ~100 KB |
| requirements.txt | Python packages | ~200 bytes |
| .streamlit/config.toml | Settings | ~500 bytes |
| README.md | Quick reference | ~50 KB |
| QUICK_START.md | User guide | ~100 KB |
| PROJECT_SUMMARY.md | Technical docs | ~150 KB |
| DEPLOYMENT_GUIDE.md | Cloud hosting | ~100 KB |

---

## ❓ Support

**Getting stuck?**

1. Check **QUICK_START.md** troubleshooting section
2. Review **PROJECT_SUMMARY.md** architecture
3. Ensure all dependencies installed: `pip list`
4. Check Python version: `python --version` (must be 3.8+)
5. Verify Streamlit works: `streamlit --version`

---

## 🎯 Next Steps

```bash
# 1. Extract and navigate
unzip medallion-swing.zip
cd medallion-swing

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run medallion_swing_unified.py

# 4. Open browser
# http://localhost:8501

# 5. Read the guides
# Start with README.md
```

---

## 📊 Verification Checklist

After installation, verify:

- [ ] Python 3.8+ installed: `python --version`
- [ ] Virtual environment active (if used)
- [ ] All packages installed: `pip list | grep -E "streamlit|pandas|plotly"`
- [ ] App starts without errors: `streamlit run medallion_swing_unified.py`
- [ ] Browser opens to http://localhost:8501
- [ ] Sidebar shows navigation buttons
- [ ] Page 1 displays leaderboard
- [ ] Page 2 search works (try "TCS")
- [ ] Page 3 portfolio metrics display
- [ ] Mock data loads (TCS, RELIANCE, INFY, HDFCBANK)

All ✓? **You're ready to trade!** 🚀

---

**Version:** 1.0  
**Status:** Production-Ready  
**Last Updated:** 2026-07-30

Enjoy the Medallion Swing Engine! 🪐
