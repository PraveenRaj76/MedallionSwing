# Start Medallion Swing with the project venv (correct deps + live SSL settings)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:MEDALLION_MARKET_MODE = "live"
$env:MEDALLION_SSL_VERIFY = "0"

$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "venv not found. Create it and pip install -r requirements.txt first."
}

Write-Host "Using: $py"
& $py -c "import curl_cffi, streamlit; print('curl_cffi OK · streamlit', streamlit.__version__)"
Write-Host "Open in browser: http://localhost:8501  (or the Local URL printed below)"
& $py -m streamlit run app.py --server.address localhost
