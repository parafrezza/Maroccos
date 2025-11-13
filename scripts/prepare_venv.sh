#!/usr/bin/env bash
set -euo pipefail

# Prepare a Python virtualenv and install headless-player requirements in a controlled way
# Useful for testing on Raspberry Pi to avoid freezes during heavy builds.

usage(){
  cat >&2 <<USAGE
Usage: $0 --target DIR [--requirements FILE] [--minimal] [--with-vlc] [--with-pyqt] [--dry-run] [--pip-extra "ARGS"]

Options:
  --target DIR       Venv directory to create (e.g., /home/pi/offplayer-venv-test)
  --requirements F   Requirements file (default: headless-player/requirements.txt)
  --minimal          Install only base deps (skip PyQt5, python-vlc). Default: ON
  --with-vlc         Include python-vlc
  --with-pyqt        Include PyQt5 (heavy on Raspberry)
  --dry-run          Download wheels only (no venv install), into ./wheelhouse
  --pip-extra ARGS   Extra args to pip (e.g., "--only-binary=:all:")

Env:
  DEBUG_VENV=1       Enable shell tracing
  PIP_PREFER_BINARY=1 and --no-cache-dir are used by default
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ_FILE="$ROOT_DIR/headless-player/requirements.txt"
TARGET=""
WITH_MINIMAL=1
WITH_VLC=0
WITH_PYQT=0
DRY_RUN=0
PIP_EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --requirements) REQ_FILE="$2"; shift 2 ;;
    --minimal) WITH_MINIMAL=1; shift ;;
    --with-vlc) WITH_VLC=1; WITH_MINIMAL=0; shift ;;
    --with-pyqt) WITH_PYQT=1; WITH_MINIMAL=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --pip-extra) PIP_EXTRA_ARGS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unexpected arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${TARGET:-}" && "$DRY_RUN" != "1" ]]; then echo "--target richiesto" >&2; usage; exit 2; fi
if [[ ! -f "$REQ_FILE" ]]; then echo "Requirements non trovato: $REQ_FILE" >&2; exit 2; fi

if [[ "${DEBUG_VENV:-0}" == "1" ]]; then set -x; fi

echo "[venv] Requirements: $REQ_FILE"
if [[ "$DRY_RUN" == "1" ]]; then echo "[venv] Modalità dry-run (download wheels)"; else echo "[venv] Target venv: $TARGET"; fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
REQ_TMP="$tmpdir/requirements.filtered.txt"
# strip comments/empty lines
grep -v -E '^[[:space:]]*#' "$REQ_FILE" | sed '/^[[:space:]]*$/d' > "$REQ_TMP"
if [[ "$WITH_MINIMAL" == "1" ]]; then
  # Drop any line starting with optional spaces then PyQt5 or python-vlc regardless of version markers
  sed -E '/^[[:space:]]*PyQt5/ d; /^[[:space:]]*python-vlc/ d' "$REQ_TMP" > "$REQ_TMP.min" || true
  mv "$REQ_TMP.min" "$REQ_TMP"
  echo "[venv] Minimal: esclusi PyQt5, python-vlc"
else
  if [[ "$WITH_VLC" == "1" ]]; then echo "[venv] Incluso python-vlc"; fi
  if [[ "$WITH_PYQT" == "1" ]]; then echo "[venv] Incluso PyQt5"; fi
fi

export PIP_PREFER_BINARY="1"
export PIP_DISABLE_PIP_VERSION_CHECK="1"

if [[ "$DRY_RUN" == "1" ]]; then
  mkdir -p wheelhouse
  echo "[venv] pip download -r $REQ_TMP -d wheelhouse $PIP_EXTRA_ARGS --no-cache-dir"
  pip3 download -r "$REQ_TMP" -d wheelhouse --no-cache-dir $PIP_EXTRA_ARGS
  echo "[venv] Download completato in ./wheelhouse"
  exit 0
fi

python3 -m venv "$TARGET"
"$TARGET/bin/pip" install -U pip wheel --no-cache-dir

echo "[venv] pip install -r $REQ_TMP $PIP_EXTRA_ARGS --no-cache-dir"
"$TARGET/bin/pip" install -r "$REQ_TMP" --no-cache-dir $PIP_EXTRA_ARGS

echo "[venv] Venv pronto: $TARGET"
