"""LED-like indicator to visualize player reachability."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPaintEvent, QPainter
from PySide6.QtWidgets import QWidget


_COLORS: dict[str, QColor] = {
    "online": QColor(46, 204, 113),
    "warning": QColor(243, 156, 18),
    "offline": QColor(231, 76, 60),
    "unknown": QColor(127, 140, 141),
    # Playlist readiness states (reuse same widget)
    "ready": QColor(46, 204, 113),
    "partial": QColor(243, 156, 18),
    "dirty": QColor(231, 76, 60),
}


class StatusIndicator(QWidget):
    """Circular colored indicator widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "unknown"
        self.setMinimumSize(QSize(16, 16))
        self.setMaximumSize(QSize(32, 32))

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(20, 20)

    def set_state(self, state: str) -> None:
        if state not in _COLORS:
            state = "unknown"
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: D401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = _COLORS.get(self._state, _COLORS["unknown"])
        painter.setBrush(color)
        painter.setPen(color.darker())
        radius = min(self.width(), self.height()) - 4
        painter.drawEllipse(2, 2, radius, radius)
        event.accept()
