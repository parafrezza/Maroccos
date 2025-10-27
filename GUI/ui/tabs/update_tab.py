"""Update and settings tab for bundle rollout and configuration."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class UpdateTab(QWidget):
    """Show player versions and expose update tools."""

    buildRequested = Signal()
    deployRequested = Signal()
    sshDeployRequested = Signal()
    frameworkChanged = Signal(str)
    autoplayToggled = Signal(bool)
    deviceNameRequested = Signal(str)
    pollIntervalChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)

        side_panel = QVBoxLayout()

        # Sposta il riepilogo/selezione dispositivi nella colonna di destra
        # (prima era al centro/sinistra della tab)
        self._inventory_label = QLabel("Seleziona i device dalla lista a sinistra")
        self._inventory_label.setWordWrap(True)
        side_panel.addWidget(self._inventory_label)

        update_box = QGroupBox("Aggiornamento")
        update_layout = QVBoxLayout(update_box)
        self._build_button = QPushButton("Build Bundle")
        self._build_button_default = self._build_button.text()
        self._build_button.clicked.connect(self.buildRequested.emit)
        self._deploy_button = QPushButton("Distribuisci")
        self._deploy_button.clicked.connect(self.deployRequested.emit)
        self._ssh_deploy_button = QPushButton("Distribuisci via SSH (fallback)")
        self._ssh_deploy_button.clicked.connect(self.sshDeployRequested.emit)
        update_layout.addWidget(self._build_button)
        update_layout.addWidget(self._deploy_button)
        update_layout.addWidget(self._ssh_deploy_button)
        self._build_progress = QProgressBar()
        self._build_progress.setVisible(False)
        self._build_progress.setRange(0, 0)
        update_layout.addWidget(self._build_progress)
        self._build_status = QLabel("Pronto a creare un bundle")
        self._build_status.setWordWrap(True)
        update_layout.addWidget(self._build_status)
        # Badge disponibilità bundle
        self._bundle_badge = QLabel("")
        self._bundle_badge.setVisible(False)
        self._bundle_badge.setStyleSheet(
            "QLabel { border-radius: 10px; padding: 2px 8px; font-weight: bold; }"
        )
        update_layout.addWidget(self._bundle_badge)
        side_panel.addWidget(update_box)

        framework_box = QGroupBox("Framework Grafico")
        framework_layout = QVBoxLayout(framework_box)
        framework_layout.addWidget(QLabel("Seleziona backend grafico per i player"))
        self._framework_selector = QComboBox()
        self._framework_options: list[str] = []
        self._framework_actions_enabled = False
        self._suppress_framework_signal = False
        self._framework_selector.currentTextChanged.connect(self._on_framework_selected)
        framework_layout.addWidget(self._framework_selector)
        side_panel.addWidget(framework_box)

        settings_box = QGroupBox("Settings")
        settings_layout = QFormLayout(settings_box)
        self._scan_ranges = QLabel("192.168.1.0/24")
        settings_layout.addRow("Reti", self._scan_ranges)
        self._media_root = QLabel("<non impostata>")
        settings_layout.addRow("Media Root", self._media_root)
        # Polling interval selector
        self._poll_selector = QComboBox()
        for ms in (500, 1000, 2000):
            self._poll_selector.addItem(f"{ms} ms", ms)
        self._poll_selector.currentIndexChanged.connect(self._on_poll_changed)
        settings_layout.addRow("Polling /status", self._poll_selector)
        side_panel.addWidget(settings_box)

        autoplay_box = QGroupBox("Autoplay")
        autoplay_layout = QVBoxLayout(autoplay_box)
        self._autoplay_toggle = QCheckBox("Abilita autoplay")
        self._autoplay_apply = QPushButton("Applica impostazione")
        self._autoplay_apply.clicked.connect(self._emit_autoplay)
        autoplay_layout.addWidget(self._autoplay_toggle)
        autoplay_layout.addWidget(self._autoplay_apply)
        side_panel.addWidget(autoplay_box)

        # Device Name box
        device_box = QGroupBox("Nome Dispositivo")
        device_layout = QVBoxLayout(device_box)
        self._device_name_current = QLabel("<sconosciuto>")
        self._device_name_button = QPushButton("Imposta nome…")
        self._device_name_button.clicked.connect(self._emit_device_name)
        device_layout.addWidget(QLabel("Imposta il nome persistente del dispositivo sui player selezionati"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Nome attuale:"))
        row.addWidget(self._device_name_current)
        row.addStretch(1)
        device_layout.addLayout(row)
        device_layout.addWidget(self._device_name_button)
        side_panel.addWidget(device_box)

        side_panel.addStretch(1)
        # Ora la tab utilizza solo la colonna di destra; rimuoviamo la colonna sinistra vuota
        layout.addLayout(side_panel, stretch=1)

        self.set_framework_state(current=None, available=[])
        # Stato bundle e abilitazione azioni
        self._bundle_available = False
        self._selection_actions_enabled = False
        self._bundle_version = None

    def set_players(self, players: list[dict]) -> None:
        count = len(players)
        sample = ", ".join(sorted({str(p.get("version", "?")) for p in players}))
        text = f"Dispositivi trovati: {count}"
        if sample:
            text += f" • Versioni: {sample}"
        self._inventory_label.setText(text)

    def set_settings(self, *, networks: str, media_root: str, polling_ms: int | None = None) -> None:
        self._scan_ranges.setText(networks)
        self._media_root.setText(media_root)
        if polling_ms is not None:
            # Select matching value; default to 1000 if not present
            idx = self._poll_selector.findData(int(polling_ms))
            if idx < 0:
                idx = self._poll_selector.findData(1000)
            if idx >= 0:
                self._poll_selector.setCurrentIndex(idx)
    def _on_poll_changed(self, _index: int) -> None:
        ms = int(self._poll_selector.currentData())
        self.pollIntervalChanged.emit(ms)

    def set_autoplay(self, enabled: bool) -> None:
        self._autoplay_toggle.setChecked(enabled)

    def enable_actions(self, enabled: bool) -> None:
        # Abilita azioni legate alla selezione nella lista (non include la disponibilità bundle)
        self._selection_actions_enabled = bool(enabled)
        self._update_actions_enabled()
        self._autoplay_toggle.setEnabled(enabled)
        self._autoplay_apply.setEnabled(enabled)
        self._device_name_button.setEnabled(enabled)
        self._framework_actions_enabled = enabled
        self._update_framework_enabled()

    def set_build_state(self, running: bool, message: str | None = None) -> None:
        self._build_button.setEnabled(not running)
        self._build_button.setText("Costruzione..." if running else self._build_button_default)
        self._build_progress.setVisible(running)
        if message:
            self._build_status.setText(message)
        elif running:
            self._build_status.setText("Costruzione bundle in corso...")
        else:
            self._build_status.setText("Pronto a creare un bundle")
        # Durante la build, consideriamo il bundle non disponibile per evitare deploy con artefatti vecchi
        if running:
            self.set_bundle_available(False)

    # La tab non espone più una tabella modificabile; la selezione è nella lista a sinistra.

    def _emit_autoplay(self) -> None:
        self.autoplayToggled.emit(self._autoplay_toggle.isChecked())

    def _emit_device_name(self) -> None:
        # Prompt handled by main window; here just emit a request with empty payload as placeholder
        # We'll emit a special value and let the main window open a dialog
        # To keep UI separation minimal, we emit an empty string and main_window will prompt.
        self.deviceNameRequested.emit("")

    def set_device_name_current(self, name: str) -> None:
        self._device_name_current.setText(name or "<sconosciuto>")

    def set_framework_state(self, *, current: str | None, available: list[str]) -> None:
        options = self._order_frameworks(list(available))
        self._framework_options = options
        self._suppress_framework_signal = True
        self._framework_selector.clear()
        if options:
            for option in options:
                self._framework_selector.addItem(option)
            if current and current in options:
                self._framework_selector.setCurrentText(current)
            else:
                self._framework_selector.setCurrentIndex(0)
        else:
            self._framework_selector.addItem("N/A")
            self._framework_selector.setCurrentIndex(0)
        self._suppress_framework_signal = False
        self._update_framework_enabled()

    # -----------------------------
    # Stato disponibilità bundle
    # -----------------------------
    def set_bundle_available(self, available: bool) -> None:
        self._bundle_available = bool(available)
        self._update_actions_enabled()
        # Aggiorna badge
        if self._bundle_available and self._bundle_version:
            # Mostra solo la versione nel badge (es. v0.1.74)
            self._bundle_badge.setText(self._bundle_version)
            self._bundle_badge.setStyleSheet(
                "QLabel { background-color: #2e7d32; color: white; border-radius: 10px; padding: 2px 8px; font-weight: bold; }"
            )
            self._bundle_badge.setVisible(True)
        else:
            # Se la versione non è nota, nascondi il badge
            self._bundle_badge.setVisible(False)

    def set_bundle_version(self, version: str | None) -> None:
        self._bundle_version = (version or None)
        # Aggiorna il badge se già visibile
        if self._bundle_available:
            self.set_bundle_available(True)

    def _update_actions_enabled(self) -> None:
        # Distribuisci via API: richiede selezione e bundle disponibile
        self._deploy_button.setEnabled(self._selection_actions_enabled and self._bundle_available)
        # Distribuisci via SSH: richiede bundle disponibile, la selezione è opzionale
        self._ssh_deploy_button.setEnabled(self._bundle_available)

    def _on_framework_selected(self, text: str) -> None:
        if self._suppress_framework_signal:
            return
        if text and text != "N/A":
            self.frameworkChanged.emit(text)

    @staticmethod
    def _order_frameworks(options: list[str]) -> list[str]:
        seen = set()
        # Mostra MPV per primo, poi CVLC, poi GST, poi PYQT; il resto in ordine alfabetico
        priority = {"mpv": 0, "cvlc": 1, "gst": 2, "pyqt": 3}
        sorted_opts = sorted(
            (opt for opt in options if opt),
            key=lambda name: (priority.get(name, 99), name),
        )
        result: list[str] = []
        for item in sorted_opts:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _update_framework_enabled(self) -> None:
        self._framework_selector.setEnabled(self._framework_actions_enabled and bool(self._framework_options))
