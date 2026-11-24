#!/bin/sh
# Start the Flute Arranger at http://localhost:5230
cd "$(dirname "$0")"
exec .venv/bin/python -m sheetswap.web.app "$@"
