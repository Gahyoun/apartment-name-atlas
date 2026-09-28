#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 backend/server.py --host 127.0.0.1 --port "${PORT:-53052}"
