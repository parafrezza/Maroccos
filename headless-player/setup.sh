#!/usr/bin/env bash
set -euo pipefail

# ========= USER VARS =========
APP_NAME="headless-player"
APP_USER="video"
APP_GROUP="video"
APP_DIR="/opt/${APP_NAME}"
APP_PORT="8080"
WIFI_SSID="extratech"
WIFI_PSK="eXtratech"
ETH_STATIC_IP="192.168.10.220/24"
ETH_IFACE_DEFAULT="eth0"
HDMI_WIDTH=1280
HDMI_HEIGHT=720
# Opzionali per mode specifico (CEA/DMT); se impostati, sovrascrivono gruppo/modo
HDMI_GROUP=${HDMI_GROUP:-1}
HDMI_MODE=${HDMI_MODE:-19}  # 1280x720@50 CEA (migliore per contenuti 25fps)
# Environment per app
USE_KMS=${USE_KMS:-1}
USE_HW_DECODER=${USE_HW_DECODER:-1}
TARGET_WIDTH=${TARGET_WIDTH:-1280}
TARGET_HEIGHT=${TARGET_HEIGHT:-720}
TARGET_FPS=${TARGET_FPS:-25/1}
# =============================

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Esegui come root: sudo $0"
    exit 1
  fi
}
require_root

echo "==> Disattivo desktop (target multi-user)"
systemctl set-default multi-user.target || true

echo "==> Crea utente/gruppo ${APP_USER}"
if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
  groupadd "${APP_GROUP}"
fi

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" --ingroup "${APP_GROUP}" "${APP_USER}"
fi

# Assicura che l'utente abbia accesso ai device DRM/KMS
if getent group render >/dev/null 2>&1; then
  usermod -a -G render "${APP_USER}" || true
fi

echo "==> Crea directory applicazione ${APP_DIR}"
mkdir -p "${APP_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

echo "==> Copio i file dell'app in ${APP_DIR}"
rsync -a --delete --exclude 'venv' --exclude 'media' --exclude '.git' --exclude '__pycache__' --exclude 'config.json' ./ "${APP_DIR}/"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

echo "==> Applico ACL permanenti per consentire scritture all'utente/gruppo dell'app"
# Default ACL: ogni nuovo file/dir eredita permessi rwx per utente/gruppo dell'app
setfacl -R -m u:${APP_USER}:rwx,g:${APP_GROUP}:rwx "${APP_DIR}" || true
setfacl -dR -m u:${APP_USER}:rwx,g:${APP_GROUP}:rwx "${APP_DIR}" || true

echo "==> Installa dipendenze di sistema (GStreamer/gi/NetworkManager)"
apt-get update
apt-get install -y \
  python3-pip python3-gi python3-gi-cairo gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-alsa \
  network-manager rsync acl || true

echo ""
echo "==> Installo backend aggiuntivi (VLC, MPV, PyQt5)"
PKG_EXTRA="vlc mpv python3-vlc python3-pyqt5 python3-pyqt5.qtmultimedia"
apt-get install -y $PKG_EXTRA || echo "[WARN] Installazione pacchetti extra fallita (continua)"


echo "==> Imposto governor CPU su 'performance'"
apt-get install -y cpufrequtils || true
if [[ -d /etc/default ]]; then
  echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils || true
  systemctl enable cpufrequtils 2>/dev/null || true
  systemctl restart cpufrequtils 2>/dev/null || true
fi

systemctl enable NetworkManager || true
systemctl start NetworkManager || true

echo "==> Crea venv e installa requirements"
su - "${APP_USER}" -c "python3 -m venv --system-site-packages ${APP_DIR}/venv"
su - "${APP_USER}" -c "${APP_DIR}/venv/bin/pip install --upgrade pip"
if [[ -f "${APP_DIR}/requirements.txt" ]]; then
  su - "${APP_USER}" -c "${APP_DIR}/venv/bin/pip install -r ${APP_DIR}/requirements.txt"
else
  su - "${APP_USER}" -c "${APP_DIR}/venv/bin/pip install fastapi uvicorn pillow netifaces"
fi

# Installa moduli python opzionali se scelti
su - "${APP_USER}" -c "${APP_DIR}/venv/bin/pip install python-vlc PyQt5 || true"

echo "==> Configuro HDMI 1280x720 e hotplug"
CFG1="/boot/firmware/config.txt"
CFG2="/boot/config.txt"
TARGET_CFG=""
if [[ -f "$CFG1" ]]; then TARGET_CFG="$CFG1"; elif [[ -f "$CFG2" ]]; then TARGET_CFG="$CFG2"; fi
if [[ -n "$TARGET_CFG" ]]; then
  sed -i '/^hdmi_force_hotplug/d' "$TARGET_CFG"
  sed -i '/^hdmi_group/d' "$TARGET_CFG"
  sed -i '/^hdmi_mode/d' "$TARGET_CFG"
  sed -i '/^disable_overscan/d' "$TARGET_CFG"
  {
    echo ""
    echo "# ${APP_NAME}: forza HDMI 1280x720"
    echo "hdmi_force_hotplug=1"
    echo "hdmi_group=${HDMI_GROUP}"
    echo "hdmi_mode=${HDMI_MODE}   # default 1280x720@50"
    echo "disable_overscan=1"
  } >> "$TARGET_CFG"
else
  echo "ATTENZIONE: non ho trovato config.txt; salto configurazione HDMI."
fi

echo "==> Provo a forzare risoluzione via cmdline (KMS)"
CMDL1="/boot/firmware/cmdline.txt"
CMDL2="/boot/cmdline.txt"
TARGET_CMD=""
if [[ -f "$CMDL1" ]]; then TARGET_CMD="$CMDL1"; elif [[ -f "$CMDL2" ]]; then TARGET_CMD="$CMDL2"; fi
if [[ -n "$TARGET_CMD" ]]; then
  if ! grep -q "video=HDMI-A-1" "$TARGET_CMD"; then
    sed -i '1 s/$/ video=HDMI-A-1:1280x720M@50D/' "$TARGET_CMD"
    echo " - Aggiunto 'video=HDMI-A-1:1280x720M@50D' a cmdline"
  else
    sed -i 's/video=HDMI-A-1:[^ ]*/video=HDMI-A-1:1280x720M@50D/' "$TARGET_CMD"
    echo " - Aggiornato parametro video= in cmdline (50Hz)"
  fi
else
  echo "(cmdline non trovato, salto forzatura KMS)"
fi

echo "==> Configuro Wi-Fi SSID '${WIFI_SSID}'"
if command -v nmcli >/dev/null 2>&1; then
  nmcli dev wifi connect "${WIFI_SSID}" password "${WIFI_PSK}" || true
else
  WPA="/etc/wpa_supplicant/wpa_supplicant.conf"
  if ! grep -q "ssid=\"${WIFI_SSID}\"" "$WPA" 2>/dev/null; then
    wpa_passphrase "${WIFI_SSID}" "${WIFI_PSK}" >> "$WPA"
  fi
  rfkill unblock wifi || true
  wpa_cli -i wlan0 reconfigure || true
fi

echo "==> Configuro IP statico su Ethernet ${ETH_STATIC_IP}"
IFACE="${ETH_IFACE_DEFAULT}"
if command -v nmcli >/dev/null 2>&1; then
  ETH_CON=$(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="ethernet"{print $1; exit}')
  if [[ -z "$ETH_CON" ]]; then
    nmcli con add type ethernet ifname "${IFACE}" con-name "Ethernet ${IFACE}" ipv4.method manual ipv4.addresses "${ETH_STATIC_IP}" ipv4.gateway "" ipv4.dns ""
  else
    nmcli con mod "$ETH_CON" ipv4.method manual ipv4.addresses "${ETH_STATIC_IP}" ipv4.gateway "" ipv4.dns ""
    nmcli con up "$ETH_CON" || true
  fi
  # Rimuovi eventuali altre connessioni ethernet per evitare profili duplicati
  mapfile -t OTHER_ETH < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="ethernet"{print $1}' | grep -v "^${ETH_CON}$")
  for C in "${OTHER_ETH[@]}"; do
    if [[ -n "$C" ]]; then
      echo " - Elimino profilo ethernet duplicato: $C"
      nmcli con delete "$C" || true
    fi
  done
else
  DHCPCD="/etc/dhcpcd.conf"
  if ! grep -q "interface ${IFACE}" "$DHCPCD" 2>/dev/null; then
    {
      echo ""
      echo "interface ${IFACE}"
      echo "static ip_address=${ETH_STATIC_IP}"
      echo "static routers="
      echo "static domain_name_servers="
    } >> "$DHCPCD"
  fi
  systemctl restart dhcpcd || true
fi

echo "==> Installo unit systemd"
install -m 0644 "${APP_DIR}/systemd/${APP_NAME}.service" "/etc/systemd/system/${APP_NAME}.service"
sed -i "s|/opt/headless-player|${APP_DIR}|g" "/etc/systemd/system/${APP_NAME}.service"
sed -i "s|--port 8080|--port ${APP_PORT}|g" "/etc/systemd/system/${APP_NAME}.service"

# Inietta variabili d'ambiente nel service
if ! grep -q "Environment=USE_KMS" "/etc/systemd/system/${APP_NAME}.service"; then
  sed -i "/^\[Service\]/a Environment=USE_KMS=${USE_KMS}\\nEnvironment=USE_HW_DECODER=${USE_HW_DECODER}\\nEnvironment=TARGET_WIDTH=${TARGET_WIDTH}\\nEnvironment=TARGET_HEIGHT=${TARGET_HEIGHT}\\nEnvironment=TARGET_FPS=${TARGET_FPS}" "/etc/systemd/system/${APP_NAME}.service"
fi

# Abilita overlay KMS di default (fade/nero a costo zero)
if ! grep -q "Environment=OVERLAY_ENABLED" "/etc/systemd/system/${APP_NAME}.service"; then
  sed -i "/^\[Service\]/a Environment=OVERLAY_ENABLED=1\nEnvironment=OVERLAY_USE_KMS=1" "/etc/systemd/system/${APP_NAME}.service"
fi

# Opzioni VLC conservative (disattiva OSD/sottotitoli auto); vout/hw accel si possono impostare in seguito
if ! grep -q "Environment=VLC_EXTRA_ARGS" "/etc/systemd/system/${APP_NAME}.service"; then
  sed -i "/^\[Service\]/a Environment=VLC_EXTRA_ARGS=--no-video-title-show --no-sub-autodetect-file --image-duration=36000" "/etc/systemd/system/${APP_NAME}.service"
fi

echo "==> Configuro sudoers per riavvio/spegnimento senza password e setup"
SUDOERS_FILE="/etc/sudoers.d/${APP_NAME}"
{
  echo "# ${APP_NAME}: privilegi necessari al servizio per reboot/poweroff e manutenzione"
  echo "${APP_USER} ALL=(root) NOPASSWD: ${APP_DIR}/setup.sh"
  echo "${APP_USER} ALL=(ALL) NOPASSWD: /sbin/poweroff, /sbin/reboot, /sbin/shutdown, /bin/systemctl poweroff, /bin/systemctl reboot, /bin/loginctl reboot, /bin/loginctl poweroff"
} > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

systemctl daemon-reload
systemctl enable "${APP_NAME}.service"

echo "==> Done."
echo " - Avvia:     sudo systemctl start ${APP_NAME}"
echo " - Reboot (consigliato per HDMI/rete): sudo reboot"

echo "==> Riavvio automatico tra 3 secondi per applicare HDMI/cmdline"
sleep 3
reboot || true
