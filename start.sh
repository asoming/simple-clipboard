#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
export PYTHONDONTWRITEBYTECODE=1
export QT_QPA_PLATFORM=xcb
export QT_XCB_GL_INTEGRATION=none
exec /usr/bin/python3 -m clipboard_app "$@"
