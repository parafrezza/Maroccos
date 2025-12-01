"""Update and settings tab for bundle rollout and configuration."""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)


class UpdateTab(QWidget):
    """Show player versions and expose update tools."""

    buildRequested = Signal()
    deployRequested = Signal()
    sshDeployRequested = Signal()
    frameworkChanged = Signal(str)
    autoplayToggled = Signal(bool)
    pollIntervalChanged = Signal(int)
    pingDurationChanged = Signal(int)
    startupListRequested = Signal()
    startupRequested = Signal()
    startupListRequested = Signal()
    bundleSelected = Signal(str)

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
        self._load_bundle_button = QPushButton("Carica nuova versione")
        self._load_bundle_button.clicked.connect(self._choose_bundle)
        self._deploy_button = QPushButton("Distribuisci")
        self._deploy_button.clicked.connect(self.deployRequested.emit)
        self._ssh_deploy_button = QPushButton("Distribuisci via SSH (fallback)")
        self._ssh_deploy_button.clicked.connect(self.sshDeployRequested.emit)
        update_layout.addWidget(self._build_button)
        update_layout.addWidget(self._load_bundle_button)
        update_layout.addWidget(self._deploy_button)
        update_layout.addWidget(self._ssh_deploy_button)
        self._build_progress = QProgressBar()
        self._build_progress.setVisible(False)
        self._build_progress.setRange(0, 0)
        update_layout.addWidget(self._build_progress)
        self._build_status = QLabel("Pronto a creare un bundle")
        self._build_status.setWordWrap(True)
        update_layout.addWidget(self._build_status)
        # Esito ultimo update
        self._update_summary = QLabel("")
        self._update_summary.setWordWrap(True)
        self._update_summary.setVisible(False)
        update_layout.addWidget(self._update_summary)
        # Badge disponibilità bundle
        self._bundle_badge = QLabel("")
        self._bundle_badge.setVisible(False)
        self._bundle_badge.setStyleSheet(
            "QLabel { border-radius: 10px; padding: 2px 8px; font-weight: bold; }"
        )
        self._bundle_badge_width = 220
        try:
            self._bundle_badge.setFixedWidth(self._bundle_badge_width)
        except Exception:
            self._bundle_badge.setMinimumWidth(self._bundle_badge_width)
        self._bundle_badge.setMinimumHeight(22)
        self._bundle_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._bundle_badge.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._bundle_badge.setWordWrap(False)
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
        settings_layout.addRow("Cartella media (file server)", self._media_root)
        # Polling interval selector
        self._poll_selector = QComboBox()
        for ms in (2000, 1000, 500):
            self._poll_selector.addItem(f"{ms} ms", ms)
        self._poll_selector.currentIndexChanged.connect(self._on_poll_changed)
        settings_layout.addRow("Polling /status", self._poll_selector)
        # Ping duration selector
        self._ping_spin = QSpinBox()
        self._ping_spin.setRange(10, 5000)
        self._ping_spin.setSingleStep(50)
        self._ping_spin.setValue(200)
        self._ping_spin.valueChanged.connect(lambda v: self.pingDurationChanged.emit(int(v)))
        settings_layout.addRow("Ping (ms)", self._ping_spin)
        self._startup_macs_button = QPushButton("Gestione MAC di avvio")
        self._startup_macs_button.clicked.connect(self.startupListRequested.emit)
        self._startup_trigger_button = QPushButton("Wake fleet")
        self._startup_trigger_button.clicked.connect(self.startupRequested.emit)
        self._startup_macs_summary = QLabel("0 MAC registrati")
        _startup_widget = QWidget()
        _startup_layout = QHBoxLayout(_startup_widget)
        _startup_layout.setContentsMargins(0, 0, 0, 0)
        _startup_layout.addWidget(self._startup_macs_button)
        _startup_layout.addWidget(self._startup_trigger_button)
        _startup_layout.addWidget(self._startup_macs_summary)
        _startup_layout.addStretch(1)
        settings_layout.addRow("MAC startup", _startup_widget)

        side_panel.addWidget(settings_box)

        autoplay_box = QGroupBox("Autoplay")
        autoplay_layout = QVBoxLayout(autoplay_box)
        self._autoplay_toggle = QCheckBox("Abilita autoplay alla partenza del player")
        self._autoplay_apply = QPushButton("Applica impostazione")
        self._autoplay_apply.clicked.connect(self._emit_autoplay)
        autoplay_layout.addWidget(self._autoplay_toggle)
        autoplay_layout.addWidget(self._autoplay_apply)
        side_panel.addWidget(autoplay_box)

        side_panel.addStretch(1)
        # Ora la tab utilizza solo la colonna di destra; rimuoviamo la colonna sinistra vuota
        layout.addLayout(side_panel, stretch=1)

        self.set_framework_state(current=None, available=[])
        # Stato bundle e abilitazione azioni
        self._bundle_available = False
        self._selection_actions_enabled = False
        self._bundle_version = None

    def _choose_bundle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona bundle", "", "Bundle Zip (*.zip);;Tutti i file (*)")
        if path:
            self.bundleSelected.emit(path)

    def set_players(self, players: list[dict]) -> None:
        count = len(players)
        sample = ", ".join(sorted({str(p.get("version", "?")) for p in players}))
        text = f"Dispositivi trovati: {count}"
        if sample:
            text += f" • Versioni: {sample}"
        self._inventory_label.setText(text)

    def set_settings(self, *, networks: str, media_root: str, polling_ms: int | None = None, ping_ms: int | None = None) -> None:
        self._scan_ranges.setText(networks)
        self._media_root.setText(media_root)
        if polling_ms is not None:
            # Select matching value; default to 2000 if not present
            idx = self._poll_selector.findData(int(polling_ms))
            if idx < 0:
                idx = self._poll_selector.findData(2000)
            if idx >= 0:
                self._poll_selector.setCurrentIndex(idx)
        if ping_ms is not None:
            try:
                self._ping_spin.blockSignals(True)
                self._ping_spin.setValue(int(ping_ms))
            finally:
                self._ping_spin.blockSignals(False)

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
            # Nascondi esito update precedente durante la build
            try:
                self._update_summary.setVisible(False)
                self._update_summary.setText("")
            except Exception:
                pass

    # La tab non espone più una tabella modificabile; la selezione è nella lista a sinistra.

    def _emit_autoplay(self) -> None:
        self.autoplayToggled.emit(self._autoplay_toggle.isChecked())

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
            display = self._format_bundle_badge_text(self._bundle_version)
            self._bundle_badge.setText(display)
            self._bundle_badge.setToolTip(self._bundle_version if display != self._bundle_version else "")
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

    def _format_bundle_badge_text(self, text: str) -> str:
        width = getattr(self, "_bundle_badge_width", None)
        if not isinstance(width, int) or width <= 0:
            try:
                width = max(80, int(self._bundle_badge.width()))
            except Exception:
                width = 140
        usable = max(24, width - 12)
        try:
            metrics = QFontMetrics(self._bundle_badge.font())
            return metrics.elidedText(text, Qt.ElideRight, usable)
        except Exception:
            return text[:32]

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

    # -----------------------------
    # Esito update (summary)
    # -----------------------------
    def show_update_summary(self, success: int, total: int, first_error: str | None = None) -> None:
        if total <= 0:
            self._update_summary.setVisible(False)
            self._update_summary.setText("")
            return
        if success == total:
            text = f"Update: {success}/{total} OK"
            style = "color: #2e7d32; font-weight: 600;"
        else:
            failed = total - success
            err = f" — primo errore: {first_error}" if first_error else ""
            text = f"Update: {success}/{total} OK, {failed} FAIL{err}"
            style = "color: #c62828; font-weight: 700;"
        try:
            self._update_summary.setText(text)
            self._update_summary.setStyleSheet(style)
            self._update_summary.setVisible(True)
        except Exception:
            pass

    def clear_update_summary(self) -> None:
        try:
            self._update_summary.setVisible(False)
            self._update_summary.setText("")
        except Exception:
            pass

    def set_startup_mac_summary(self, count: int) -> None:
        if count <= 0:
            text = "Nessun MAC registrato"
        else:
            text = f"{count} MAC registrati"
        self._startup_macs_summary.setText(text)
