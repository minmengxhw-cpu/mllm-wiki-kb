#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi

"$PYTHON_BIN" -B -m unittest discover -s tests
"$PYTHON_BIN" -m compileall -q src tests webapp

if PYTHONPATH=src "$PYTHON_BIN" -m kb.cli check "沈均儒参与民盟特设支部工作，中国民主同盟成立于1941年3月19日。"; then
  echo "kb check should fail on high-risk draft text"
  exit 1
fi
