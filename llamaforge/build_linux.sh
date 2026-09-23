#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --name LlamaForge --add-data "llamaforge/web/static:llamaforge/web/static" run.py
echo "Build complete: dist/LlamaForge/"
