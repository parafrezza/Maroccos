#!/usr/bin/env bash
set -euo pipefail

# Prune old release artifacts in a directory, keeping the most recent N files
# Default pattern targets OFF-player release tarballs.

usage(){
  cat >&2 <<USAGE
Usage: $0 [--dir DIR] [--keep N] [--pattern 'glob'] [--commit]

Options:
  --dir DIR       Directory with releases (default: ./release)
  --keep N        How many most-recent files to keep (default: 5)
  --pattern GLOB  Shell glob of files to prune (default: 'OFF-player-release-*.tar.gz')
  --commit        Actually delete files (dry-run by default)

Examples:
  $0                            # dry-run in ./release keep 5
  $0 --keep 10 --commit         # delete older than last 10 (in ./release)
  $0 --dir /path/release --pattern '*.tgz' --commit
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIR="$ROOT_DIR/release"
KEEP=5
PATTERN='OFF-player-release-*.tar.gz'
COMMIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --pattern) PATTERN="$2"; shift 2 ;;
    --commit) COMMIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unexpected arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -d "$DIR" ]]; then
  echo "[prune] Directory non esistente: $DIR" >&2
  exit 0
fi

shopt -s nullglob
files=("$DIR"/$PATTERN)
shopt -u nullglob

if (( ${#files[@]} == 0 )); then
  echo "[prune] Nessun file che corrisponde a '$PATTERN' in $DIR"
  exit 0
fi

# Sort by mtime descending
mapfile -t sorted < <(ls -1t -- "${files[@]}")

if (( ${#sorted[@]} <= KEEP )); then
  echo "[prune] Trovati ${#sorted[@]} file, nulla da rimuovere (keep=$KEEP)"
  exit 0
fi

# Determine victims beyond KEEP
victims=("${sorted[@]:$KEEP}")

echo "[prune] Directory: $DIR"
echo "[prune] Pattern:   $PATTERN"
printf '[prune] Keep %d file più recenti:\n' "$KEEP"
for f in "${sorted[@]:0:$KEEP}"; do
  echo "  KEEP  $(basename "$f")"
done

echo "[prune] Candidati alla rimozione (${#victims[@]}):"
for f in "${victims[@]}"; do
  echo "  DELETE $(basename "$f")"
  if (( COMMIT == 1 )); then
    rm -f -- "$f"
  fi
done

if (( COMMIT == 1 )); then
  echo "[prune] Rimozione completata"
else
  echo "[prune] Dry-run: nessun file rimosso. Aggiungi --commit per applicare."
fi
