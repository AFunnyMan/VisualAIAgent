$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw '请先安装 uv，参阅 README.md。'
}
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache' }
if (-not $env:UV_PYTHON_INSTALL_DIR) { $env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.python' }
uv sync --locked --group dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --locked streamlit run app.py --server.address 127.0.0.1
exit $LASTEXITCODE
