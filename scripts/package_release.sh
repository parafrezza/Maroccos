#!/usr/bin/env bash
set -euo pipefail

# Script to build OFF-player (Release) and produce a single tar.gz containing
# - OFF-player binary (bin/OFF-player)
# - headless-player directory
# - a systemd unit template
# - README with install instructions

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT/OFF-player"
HEADLESS_DIR="$ROOT/headless-player"
OUT_DIR="$ROOT/release"
PKGNAME="OFF-player-release-$(date +%Y%m%d-%H%M%S)"
PACKAGE_ALLOW_MISSING_OF_SO=1

mkdir -p "$OUT_DIR"
if [ ! -w "$OUT_DIR" ]; then
    echo "Directory di output non scrivibile: $OUT_DIR (forse creato con sudo)." >&2
    echo "Rendi la cartella scrivibile (es. chown -R $(whoami): $OUT_DIR) oppure esegui lo script con i permessi corretti." >&2
    exit 9
fi
echo "Building OFF-player (Release)..."
# Smart check: avoid rebuilding if already up-to-date
if make -C "$BUILD_DIR" -q Release >/dev/null 2>&1; then
    echo "Target Release già aggiornato. Salto la compilazione."
    BUILD_LOG=""
else
    # Build and capture log to detect masked errors
    BUILD_LOG="$OUT_DIR/build-$(date +%Y%m%d-%H%M%S).log"
    if ! make -C "$BUILD_DIR" -j4 Release 2>&1 | tee "$BUILD_LOG"; then
        echo "Build fallita. Vedi log: $BUILD_LOG" >&2
        exit 10
    fi
fi

# Detect common masked cp error from OF makefiles (copying .so that may not exist)
if [[ -n "${BUILD_LOG:-}" ]] && grep -qE "cp: cannot stat '.*openFrameworks/libs/.*/lib/linuxaarch64/.*\\.so'" "$BUILD_LOG"; then
    echo "Rilevato errore cp di .so nel log di build (probabile build statica)." >&2
    echo "Per sicurezza interrompo il packaging. Esporta PACKAGE_ALLOW_MISSING_OF_SO=1 per ignorare." >&2
    if [ "${PACKAGE_ALLOW_MISSING_OF_SO:-0}" != "1" ]; then
        exit 11
    else
        echo "Ignoro l'errore cp perché PACKAGE_ALLOW_MISSING_OF_SO=1 è impostato." >&2
    fi
fi

echo "Collecting files into $OUT_DIR/$PKGNAME..."
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

mkdir -p "$TMPDIR/$PKGNAME"

# copy executable
if [ -f "$BUILD_DIR/bin/OFF-player" ]; then
    mkdir -p "$TMPDIR/$PKGNAME/bin"
    cp "$BUILD_DIR/bin/OFF-player" "$TMPDIR/$PKGNAME/bin/"
else
    echo "Error: OFF-player binary not found after build" >&2
    exit 2
fi

# copy headless-player directory (required)
if [ -d "$HEADLESS_DIR" ]; then
    cp -a "$HEADLESS_DIR" "$TMPDIR/$PKGNAME/"
else
    echo "Errore: directory headless-player non trovata" >&2
    exit 12
fi

# include systemd template, deploy README, and provisioning scripts
mkdir -p "$TMPDIR/$PKGNAME/resources"
mkdir -p "$TMPDIR/$PKGNAME/scripts"
cp "$ROOT/resources/headless-player.service" "$TMPDIR/$PKGNAME/resources/" || true
cp "$ROOT/DEPLOY.md" "$TMPDIR/$PKGNAME/" || true
cp "$ROOT/scripts/install_on_pi.sh" "$TMPDIR/$PKGNAME/scripts/" || true
cp "$ROOT/scripts/provision_pi.sh" "$TMPDIR/$PKGNAME/scripts/" || true
chmod +x "$TMPDIR/$PKGNAME/scripts/"*.sh 2>/dev/null || true

echo "Creating tarball..."
tar -C "$TMPDIR" -czf "$OUT_DIR/${PKGNAME}.tar.gz" "$PKGNAME"

echo "Release created: $OUT_DIR/${PKGNAME}.tar.gz"

exit 0
