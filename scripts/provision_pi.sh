#!/usr/bin/env bash
set -euo pipefail

# Provisioning Raspberry Pi per OFF Player in modalità headless/kiosk
# - Disabilita desktop grafico (multi-user.target)
# - Configura autologin su console per utente root
# - Disabilita screensaver/blanking
# - Imposta sfondo nero se desktop è abilitato
# - Ottimizza servizi e CPU governor
# - Configura Wi-Fi e IP statico Ethernet
# - Configura HDMI 1280x720@50Hz

# ========= USER VARS =========
WIFI_SSID="${WIFI_SSID:-extratech}"
WIFI_PSK="${WIFI_PSK:-eXtratech}"
ETH_STATIC_IP="${ETH_STATIC_IP:-192.168.10.220/24}"
ETH_IFACE="${ETH_IFACE:-eth0}"
HDMI_WIDTH="${HDMI_WIDTH:-1280}"
HDMI_HEIGHT="${HDMI_HEIGHT:-720}"
HDMI_GROUP="${HDMI_GROUP:-1}"  # 1=CEA, 2=DMT
HDMI_MODE="${HDMI_MODE:-19}"   # 1280x720@50Hz CEA
AUTOLOGIN_USER="${AUTOLOGIN_USER:-root}"
# =============================

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Esegui come root: sudo $0"
    exit 1
  fi
}
require_root

log() { echo "[provision] $*"; }

log "==> Provisioning Raspberry Pi per OFF Player"

# 1. Disabilita desktop grafico (target multi-user)
log "==> Disabilita desktop grafico (multi-user.target)"
systemctl set-default multi-user.target || true

# 2. Configura autologin su console
log "==> Configura autologin console per utente ${AUTOLOGIN_USER}"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${AUTOLOGIN_USER} --noclear %I \$TERM
EOF
systemctl daemon-reload

# 3. Disabilita screensaver e blanking della console
log "==> Disabilita blanking console"
# Aggiungi parametri kernel per disabilitare blanking
CMDL1="/boot/firmware/cmdline.txt"
CMDL2="/boot/cmdline.txt"
TARGET_CMD=""
if [[ -f "$CMDL1" ]]; then TARGET_CMD="$CMDL1"; elif [[ -f "$CMDL2" ]]; then TARGET_CMD="$CMDL2"; fi

if [[ -n "$TARGET_CMD" ]]; then
  # Rimuovi eventuali consoleblank esistenti e aggiungi consoleblank=0
  sed -i 's/consoleblank=[0-9]*//g' "$TARGET_CMD"
  sed -i '1 s/$/ consoleblank=0/' "$TARGET_CMD"
  log " - Aggiunto 'consoleblank=0' a cmdline"
fi

# Disabilita blanking anche a runtime
if [[ -f /usr/bin/setterm ]]; then
  setterm -blank 0 -powerdown 0 -powersave off 2>/dev/null || true
fi

# 4. Se esiste X11, configura per sfondo nero e no screensaver
log "==> Configura X11 (se presente) per sfondo nero e no screensaver"
# Crea .xinitrc globale
cat > /etc/X11/xinit/xinitrc <<'XEOF'
#!/bin/sh
# Sfondo nero
xsetroot -solid black
# Disabilita screensaver e blanking
xset s off
xset s noblank
xset -dpms
# Nasconde cursore dopo 1 secondo di inattività
unclutter -idle 1 &
exec openbox-session
XEOF
chmod +x /etc/X11/xinit/xinitrc

# Installa unclutter per nascondere il cursore (se non presente)
if ! command -v unclutter >/dev/null 2>&1; then
  log " - Installo unclutter per nascondere cursore"
  apt-get update -qq
  apt-get install -y -qq unclutter 2>/dev/null || true
fi

# 5. Ottimizza CPU governor
log "==> Imposta CPU governor su 'performance'"
apt-get install -y -qq cpufrequtils 2>/dev/null || true
if [[ -d /etc/default ]]; then
  echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils || true
  systemctl enable cpufrequtils 2>/dev/null || true
  systemctl restart cpufrequtils 2>/dev/null || true
fi

# 6. Disabilita servizi non necessari
log "==> Disabilita servizi non essenziali"
SERVICES_TO_DISABLE=(
  "triggerhappy"
  "bluetooth"
  "hciuart"
  "avahi-daemon"
  "cups"
  "cups-browsed"
)
for svc in "${SERVICES_TO_DISABLE[@]}"; do
  if systemctl list-unit-files | grep -q "^${svc}.service"; then
    systemctl disable "${svc}.service" 2>/dev/null || true
    systemctl stop "${svc}.service" 2>/dev/null || true
    log " - Disabilitato: $svc"
  fi
done

# 7. Configura HDMI
log "==> Configura HDMI ${HDMI_WIDTH}x${HDMI_HEIGHT}@50Hz"
CFG1="/boot/firmware/config.txt"
CFG2="/boot/config.txt"
TARGET_CFG=""
if [[ -f "$CFG1" ]]; then TARGET_CFG="$CFG1"; elif [[ -f "$CFG2" ]]; then TARGET_CFG="$CFG2"; fi

if [[ -n "$TARGET_CFG" ]]; then
  # Rimuovi configurazioni HDMI esistenti
  sed -i '/^hdmi_force_hotplug/d' "$TARGET_CFG"
  sed -i '/^hdmi_group/d' "$TARGET_CFG"
  sed -i '/^hdmi_mode/d' "$TARGET_CFG"
  sed -i '/^disable_overscan/d' "$TARGET_CFG"
  
  # Aggiungi nuova configurazione
  cat >> "$TARGET_CFG" <<EOF

# OFF Player: configurazione HDMI
hdmi_force_hotplug=1
hdmi_group=${HDMI_GROUP}
hdmi_mode=${HDMI_MODE}
disable_overscan=1
EOF
  log " - Configurazione HDMI aggiunta a $TARGET_CFG"
else
  log "ATTENZIONE: config.txt non trovato, salto configurazione HDMI"
fi

# Forza risoluzione KMS via cmdline
if [[ -n "$TARGET_CMD" ]]; then
  if ! grep -q "video=HDMI-A-1" "$TARGET_CMD"; then
    sed -i "1 s/$/ video=HDMI-A-1:${HDMI_WIDTH}x${HDMI_HEIGHT}M@50D/" "$TARGET_CMD"
    log " - Aggiunto parametro video KMS a cmdline"
  else
    sed -i "s/video=HDMI-A-1:[^ ]*/video=HDMI-A-1:${HDMI_WIDTH}x${HDMI_HEIGHT}M@50D/" "$TARGET_CMD"
    log " - Aggiornato parametro video KMS in cmdline"
  fi
fi

# 8. Configura Wi-Fi
log "==> Configura Wi-Fi SSID '${WIFI_SSID}'"
if command -v nmcli >/dev/null 2>&1; then
  nmcli dev wifi connect "${WIFI_SSID}" password "${WIFI_PSK}" 2>/dev/null || log " - Connessione Wi-Fi esistente o errore"
else
  WPA="/etc/wpa_supplicant/wpa_supplicant.conf"
  if ! grep -q "ssid=\"${WIFI_SSID}\"" "$WPA" 2>/dev/null; then
    wpa_passphrase "${WIFI_SSID}" "${WIFI_PSK}" >> "$WPA"
    log " - Aggiunta rete Wi-Fi a wpa_supplicant.conf"
  fi
  rfkill unblock wifi 2>/dev/null || true
  wpa_cli -i wlan0 reconfigure 2>/dev/null || true
fi

# 9. Configura IP statico Ethernet
log "==> Configura IP statico Ethernet ${ETH_STATIC_IP}"
if command -v nmcli >/dev/null 2>&1; then
  # Usa NetworkManager
  ETH_CON=$(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="ethernet"{print $1; exit}')
  if [[ -z "$ETH_CON" ]]; then
    nmcli con add type ethernet ifname "${ETH_IFACE}" con-name "Ethernet ${ETH_IFACE}" \
      ipv4.method manual ipv4.addresses "${ETH_STATIC_IP}" ipv4.gateway "" ipv4.dns "" 2>/dev/null || true
  else
    nmcli con mod "$ETH_CON" ipv4.method manual ipv4.addresses "${ETH_STATIC_IP}" \
      ipv4.gateway "" ipv4.dns "" 2>/dev/null || true
    nmcli con up "$ETH_CON" 2>/dev/null || true
  fi
  log " - IP statico configurato via NetworkManager"
else
  # Usa dhcpcd
  DHCPCD="/etc/dhcpcd.conf"
  if ! grep -q "interface ${ETH_IFACE}" "$DHCPCD" 2>/dev/null; then
    cat >> "$DHCPCD" <<EOF

# OFF Player: IP statico Ethernet
interface ${ETH_IFACE}
static ip_address=${ETH_STATIC_IP}
static routers=
static domain_name_servers=
EOF
    log " - IP statico configurato via dhcpcd"
    systemctl restart dhcpcd 2>/dev/null || true
  fi
fi

# 10. Configura firewall (se ufw è presente)
if command -v ufw >/dev/null 2>&1; then
  log "==> Configura regole firewall base"
  ufw allow 8080/tcp comment 'headless-player HTTP' 2>/dev/null || true
  ufw allow 7777/udp comment 'headless-player UDP' 2>/dev/null || true
  ufw allow 22/tcp comment 'SSH' 2>/dev/null || true
  log " - Regole firewall configurate"
fi

# 11. Disabilita aggiornamenti automatici (opzionale)
log "==> Disabilita aggiornamenti automatici"
systemctl disable apt-daily.timer 2>/dev/null || true
systemctl disable apt-daily-upgrade.timer 2>/dev/null || true
systemctl stop apt-daily.timer 2>/dev/null || true
systemctl stop apt-daily-upgrade.timer 2>/dev/null || true

log "==> Provisioning completato"
log ""
log "PROSSIMI PASSI:"
log "1. Riavvia il sistema: sudo reboot"
log "2. Dopo il riavvio, il sistema partirà in modalità console (multi-user)"
log "3. Login automatico come '${AUTOLOGIN_USER}'"
log "4. headless-player sarà già attivo (se installato)"
log ""
log "Per verificare: systemctl status headless-player.service"
