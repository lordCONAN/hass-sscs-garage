#!/bin/bash
# Run the Home Assistant integration tests (tests/ha) on Linux or in WSL.
#
# Home Assistant does not run on Windows (it needs fcntl and Linux-only Bluetooth
# packages), and 2026.10 needs Python >= 3.14.2. From Windows:
#
#   wsl bash tools/test-ha.sh            (extra arguments go to pytest)
#
# First run creates ~/venvs/sscs-garage-ha with uv (installed for the user if missing).
set -euo pipefail

VENV="${SSCS_HA_VENV:-$HOME/venvs/sscs-garage-ha}"
cd "$(dirname "$0")/.."

if [ ! -x "$VENV/bin/pytest" ]; then
  if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  export PATH="$HOME/.local/bin:$PATH"
  uv venv --python 3.14 "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install pytest pytest-homeassistant-custom-component
  # HA installs component requirements at runtime; the tests import bluetooth (and usb)
  # directly, so install the versions their manifests pin.
  reqs=$("$VENV/bin/python" - <<'EOF'
import json, pathlib, homeassistant.components as c
base = pathlib.Path(c.__file__).parent
reqs = [r for comp in ("bluetooth", "usb") for r in json.loads((base / comp / "manifest.json").read_text())["requirements"]]
print(" ".join(dict.fromkeys(reqs)))
EOF
)
  VIRTUAL_ENV="$VENV" uv pip install $reqs
fi

# -p no:cacheprovider keeps .pytest_cache off the Windows drive.
exec "$VENV/bin/python" -m pytest -p no:cacheprovider "$@"
