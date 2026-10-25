#!/bin/sh
set -eu
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
    fi
    exec .venv/bin/python scripts/setup.py "$@"
fi
printf '%s\n' '请先安装 Python 3.10 或更高版本，然后重新运行本脚本。' >&2
exit 1
