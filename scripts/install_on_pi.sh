#!/usr/bin/env bash
set -euo pipefail

# Install a release tarball built by scripts/package_release.sh
# - Extracts into PREFIX (default: /opt/offplayer)
# - Creates a Python venv for headless-player and installs requirements
# - Installs/updates systemd unit and restarts the service

usage() {
  cat >&2 <<USAGE
Usage: sudo $0 <tarball.tgz> [--prefix /opt/offplayer] [--minimal] [--with-vlc] [--with-pyqt] [--pip-extra "ARGS"]

Opzioni:
  --prefix DIR     Percorso di installazione (default: /opt/offplayer)
  --minimal        Installa solo dipendenze base (salta backend opzionali pesanti). Default: ON
  --with-vlc       Include python-vlc dal requirements
  --with-pyqt      Include PyQt5 dal requirements (pesante su Raspberry)
  --pip-extra ARGS Argomenti aggiuntivi per pip install (es: "--only-binary=:all:")

Ambiente:
  DEBUG_INSTALL=1  Traccia comandi eseguiti (set -x)
  PIP_PREFER_BINARY=1 e --no-cache-dir sono abilitati di default
USAGE
}

PREFIX="/opt/offplayer"
TARBALL=""
WITH_MINIMAL=1
WITH_VLC=0
WITH_PYQT=0
PIP_EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="$2"; shift 2 ;;
    --minimal)
      WITH_MINIMAL=1; shift ;;
    --with-vlc)
      WITH_VLC=1; WITH_MINIMAL=0; shift ;;
    --with-pyqt)
      WITH_PYQT=1; WITH_MINIMAL=0; shift ;;
    --pip-extra)
      PIP_EXTRA_ARGS="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      if [[ -z "$TARBALL" ]]; then TARBALL="$1"; shift; else echo "Unexpected arg: $1" >&2; usage; exit 2; fi ;;
  esac
done

# Optional verbose tracing
if [[ "${DEBUG_INSTALL:-0}" == "1" ]]; then
  set -x
fi

if [[ -z "${TARBALL:-}" ]]; then usage; exit 2; fi

echo "[install] Avvio installazione"
echo "[install] Tarball: $TARBALL"
echo "[install] Prefix:  $PREFIX"

if [[ ! -r "$TARBALL" ]]; then echo "[install] ERRORE: Tarball non leggibile o inesistente: $TARBALL" >&2; exit 2; fi

log(){ echo -e "[install] $*"; }

# Determine top-level dir inside tarball
TOPDIR=$(tar -tzf "$TARBALL" | head -1 | cut -d/ -f1)
if [[ -z "$TOPDIR" ]]; then echo "Tarball vuoto/invalid: $TARBALL" >&2; exit 3; fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

log "Extracting $TARBALL ..."
tar -C "$TMPDIR" -xzf "$TARBALL"

# Safety: ensure expected structure exists
if [[ ! -d "$TMPDIR/$TOPDIR/headless-player" ]]; then
  echo "[install] ERRORE: Struttura attesa non trovata (headless-player mancante)" >&2
  exit 4
fi
if [[ ! -x "$TMPDIR/$TOPDIR/bin/OFF-player" ]]; then
  log "ATTENZIONE: binario OFF-player non trovato o non eseguibile nel pacchetto. Procedo comunque."
fi

log "Installing to $PREFIX ..."
mkdir -p "$PREFIX"
# Move contents (prefer rsync if available)
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$TMPDIR/$TOPDIR/" "$PREFIX/"
else
  # Fallback: remove existing and copy
  rm -rf "$PREFIX"/*
  cp -a "$TMPDIR/$TOPDIR/." "$PREFIX/"
fi

# Cleanup python caches from previous runs, if any
log "Pulizia cache Python (__pycache__) pre-install..."
find "$PREFIX/headless-player" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

# Install/Update systemd unit as early as possible (before Python deps)
UNIT_SRC="$PREFIX/resources/headless-player.service"
UNIT_DST="/etc/systemd/system/headless-player.service"
if [[ -f "$UNIT_SRC" ]]; then
  log "Installing systemd unit ..."
  sed "s#__INSTALL_DIR__#${PREFIX}#g" "$UNIT_SRC" > "$UNIT_DST"
  log "systemctl daemon-reload"
  systemctl daemon-reload
  log "systemctl enable headless-player.service"
  systemctl enable headless-player.service || true
else
  log "Unit systemd non presente nel pacchetto: $UNIT_SRC (proseguo senza aggiornare la unit)"
fi

# Python venv setup for headless-player
if [[ -d "$PREFIX/headless-player" ]]; then
  log "Preparazione venv Python ..."
  if ! command -v python3 >/dev/null 2>&1; then echo "python3 mancante" >&2; exit 5; fi

  # Evita crash loop durante (re)creazione venv
  log "Arresto servizio headless-player (se in esecuzione)"
  systemctl stop headless-player.service || true

  pushd "$PREFIX/headless-player" >/dev/null
  # Ensure a fresh venv each install
  if [[ -d .venv ]]; then
    log "Rimuovo venv precedente (.venv)"
    rm -rf .venv
  fi
  python3 -m venv .venv
  PIP_BIN="$PWD/.venv/bin/pip"
  "$PIP_BIN" install -U pip wheel --no-cache-dir || log "AVVISO: upgrade pip/wheel non riuscito, proseguo"
  if [[ -f requirements.txt ]]; then
    REQ_SRC="requirements.txt"
    REQ_TMP=".requirements.filtered.txt"
    # rimuovi commenti e righe vuote
    grep -v -E '^[[:space:]]*#' "$REQ_SRC" | sed '/^[[:space:]]*$/d' > "$REQ_TMP"
    if [[ "$WITH_MINIMAL" == "1" ]]; then
      # escludi backend opzionali pesanti di default (PyQt5, python-vlc)
      sed -E '/^[[:space:]]*PyQt5/ d; /^[[:space:]]*python-vlc/ d' "$REQ_TMP" > "$REQ_TMP.min" || true
      mv "$REQ_TMP.min" "$REQ_TMP"
      log "Modalità minimal: esclusi backend opzionali (PyQt5, python-vlc)"
    else
      if [[ "$WITH_VLC" == "1" ]]; then log "Incluso python-vlc"; fi
      if [[ "$WITH_PYQT" == "1" ]]; then log "Incluso PyQt5"; fi
      # se minimal=0 senza flag, includi tutto
    fi
    export PIP_PREFER_BINARY="1"
    export PIP_DISABLE_PIP_VERSION_CHECK="1"
    log "pip install -r $REQ_TMP $PIP_EXTRA_ARGS --no-cache-dir"
    if ! "$PIP_BIN" install -r "$REQ_TMP" --no-cache-dir $PIP_EXTRA_ARGS; then
      echo "[install] AVVISO: pip install fallito: il servizio non verrà avviato automaticamente" >&2
      PIP_FAILED=1
    fi
  else
    log "requirements.txt non trovato: nessuna dipendenza Python installata"
  fi
  popd >/dev/null

  # Riavvia servizio solo se pip è riuscito o se non servono deps
  if [[ "${PIP_FAILED:-0}" != "1" ]]; then
    log "Riavvio servizio headless-player"
    systemctl restart headless-player.service || true
  else
    log "Salto riavvio servizio: completare installazione dipendenze e avviare con: systemctl restart headless-player"
  fi
fi

# Permissions (world-writable by default for simplicity in kiosk setups)
mkdir -p "$PREFIX/media" || true
chmod 0777 "$PREFIX/media" || true
if [[ -f "$PREFIX/headless-player/config.json" ]]; then
  chmod 0666 "$PREFIX/headless-player/config.json" || true
fi
mkdir -p /var/tmp/headless-player
chmod 0777 /var/tmp/headless-player || true

log "Installazione completata in $PREFIX"
if [[ "${PIP_FAILED:-0}" == "1" ]]; then
  echo "[install] AVVISO: alcune dipendenze Python non sono state installate. Puoi riprovare più tardi con:" >&2
  echo "[install]   source $PREFIX/headless-player/.venv/bin/activate && pip install -r $PREFIX/headless-player/requirements.txt" >&2
fi

# Esegui provisioning del sistema se disponibile
PROVISION_SCRIPT="$(dirname "$0")/provision_pi.sh"
if [[ -f "$PROVISION_SCRIPT" && -x "$PROVISION_SCRIPT" ]]; then
  log "Esecuzione provisioning del sistema..."
  if "$PROVISION_SCRIPT"; then
    log "Provisioning completato con successo"
  else
    log "AVVISO: provisioning fallito (codice: $?)"
  fi
elif [[ -f "$PROVISION_SCRIPT" ]]; then
  log "AVVISO: script di provisioning trovato ma non eseguibile: $PROVISION_SCRIPT"
  log "Esegui manualmente: sudo bash $PROVISION_SCRIPT"
else
  log "AVVISO: script di provisioning non trovato: $PROVISION_SCRIPT"
  log "Il sistema non è stato configurato per l'auto-partenza e desktop nero."
  log "Per configurare manualmente, esegui: sudo bash $(dirname "$0")/provision_pi.sh"
fi
