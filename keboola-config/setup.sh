#!/bin/bash
set -euo pipefail

cd /app
echo "Installing dependencies ($(python3 -V))..."

# --no-build turns a missing wheel into a fast, explicit failure.
#
# - setup.sh runs before nginx and supervisord, so anything slow here leaves the pod
#   unready on port 8888 with no error to explain it.
# - A source build of a compiled dependency takes minutes on one core and the readiness
#   probe gives up long before it finishes.
if ! uv sync --no-build; then
    echo "[ERROR] A dependency has no prebuilt wheel for $(python3 -V). Raise the floors in pyproject.toml, or deploy a runtime whose Python it supports." >&2
    exit 1
fi

echo "Setup complete."
