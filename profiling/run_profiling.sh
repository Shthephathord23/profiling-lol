#!/usr/bin/env bash
# Thin shim around the Python core. All logic lives in lib/cli.py.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/cli.py" "$@"
