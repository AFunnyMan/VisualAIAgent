#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if command -v uv >/dev/null 2>&1; then
    VAA_UV=uv
elif [ -x .tools/bin/uv ]; then
    VAA_UV=.tools/bin/uv
else
    echo '请先安装 uv，参阅 README.md。' >&2
    exit 1
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.python}"
"$VAA_UV" sync --locked --group dev
exec "$VAA_UV" run --locked streamlit run app.py --server.address 127.0.0.1
