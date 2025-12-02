"""Tab di test per inviare comandi UDP ai player e a un endpoint LoRa TX."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# Comandi UDP supportati (plain text) e se richiedono input
_UDP_COMMANDS: list[dict[str, Any]] = [
    {"label": "STATUS", "command": "STATUS"},
    {"label": "PLAY", "command": "PLAY"},
    {"label": "STOP", "command": "STOP"},
    {"label": "PAUSE", "command": "PAUSE"},
    {"label": "RESUME", "command": "RESUME"},
    {"label": "NEXT", "command": "NEXT"},
    {"label": "PREV", "command": "PREV"},
    {"label": "LOOP ON", "command": "LOOP on"},
    {"label": "LOOP OFF", "command": "LOOP off"},
    {"label": "GOTO START", "command": "GO_TO_START"},
    {"label": "PLAYFILE…", "command": "PLAYFILE", "prompt": "Filename o path"},
    {"label": "SET INDEX…", "command": "SET", "prompt": "Indice zero-based"},
    {"label": "JUMP TRACK…", "command": "JUMP", "prompt": "Numero traccia (1-based)"},
    {"label": "PLAYLIST STATUS", "command": "PLAYLIST STATUS"},
    {"label": "AUTOPLAY ON", "command": "AUTOPLAY on"},
    {"label": "AUTOPLAY OFF", "command": "AUTOPLAY off"},
    {"label": "FADE DEFAULT…", "command": "FADE", "prompt": "Durata fade default (s)"},
    {"label": "BRIGHTNESS…", "command": "BRIGHTNESS", "prompt": "Valore (0..1 o 0..100) opz. seconds"},
    {"label": "STARTUP", "command": "STARTUP"},
]


class TestUdpTab(QWidget):
    """Tab minimale per inviare comandi UDP."""

    udpCommandRequested = Signal(str, str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Lora-TX IP:"))
        self._lora_ip = QLineEdit("192.168.8.200")
        self._lora_ip.setPlaceholderText("IP destinazione aggiuntivo")
        self._lora_ip.setMaximumWidth(180)
        header.addWidget(self._lora_ip)
        header.addSpacing(12)
        header.addWidget(QLabel("Porta UDP:"))
        self._udp_port = QLineEdit("7777")
        self._udp_port.setMaximumWidth(80)
        self._udp_port.setToolTip("Porta UDP (default 7777)")
        header.addWidget(self._udp_port)
        header.addStretch(1)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(8)
        columns = 3
        for idx, meta in enumerate(_UDP_COMMANDS):
            btn = QPushButton(meta["label"])
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=meta: self._emit_command(m))
            row = idx // columns
            col = idx % columns
            grid.addWidget(btn, row, col)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        outer.addStretch(1)

    def _emit_command(self, meta: dict[str, Any]) -> None:
        cmd = meta.get("command", "")
        if not cmd:
            return
        if meta.get("prompt"):
            text, ok = QInputDialog.getText(self, meta["label"], meta["prompt"])
            if not ok or not text.strip():
                return
            cmd = f"{cmd} {text.strip()}"
        port = 7777
        try:
            port = int(self._udp_port.text())
        except Exception:
            port = 7777
        self.udpCommandRequested.emit(cmd, self._lora_ip.text().strip(), port)
