#!/usr/bin/env bash
set -euo pipefail
python3.11 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
pre-commit install
echo "Venv ready. Activate with: . .venv/bin/activate"
