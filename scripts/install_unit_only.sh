#!/usr/bin/env bash
set -euo pipefail

PREFIX="/opt/offplayer"
UNIT_SRC="$PREFIX/resources/headless-player.service"
UNIT_DST="/etc/systemd/system/headless-player.service"

if [[ ${1:-} == "--prefix" ]]; then
  PREFIX="${2:-$PREFIX}"; shift 2 || true
  UNIT_SRC="$PREFIX/resources/headless-player.service"
fi

echo "[unit] Source: $UNIT_SRC"
echo "[unit] Dest:   $UNIT_DST"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "[unit] ERRORE: unit sorgente non trovata: $UNIT_SRC" >&2
  exit 2
fi

sed "s#__INSTALL_DIR__#${PREFIX}#g" "$UNIT_SRC" | sudo tee "$UNIT_DST" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable headless-player.service || true
sudo systemctl restart headless-player.service || true
sudo systemctl status --no-pager --lines=30 headless-player.service || true

echo "[unit] Aggiornamento unit completato"
