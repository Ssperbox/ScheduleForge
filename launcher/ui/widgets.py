"""Reusable widgets: cards, badges, log view and a drag-and-drop table."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """Rounded panel used for the header / status sections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.NoFrame)


class Badge(QLabel):
    """Small status pill whose colour follows its state."""

    def __init__(self, text: str = "", state: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        object_name = {
            "ok": "BadgeOk",
            "warn": "BadgeWarn",
            "bad": "BadgeBad",
        }.get(state, "BadgeNeutral")
        if self.objectName() != object_name:
            self.setObjectName(object_name)
            style = self.style()
            style.unpolish(self)
            style.polish(self)


class LogView(QPlainTextEdit):
    """Read-only coloured activity log with a bounded number of blocks."""

    COLORS = {
        "warn": "#d29a3a",
        "error": "#e05c5c",
        "success": "#5aa84a",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.document().setMaximumBlockCount(2500)
        self.setMinimumHeight(110)

    def append_line(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        level = level if level in ("info", "warn", "error", "success") else "info"
        tag = {"warn": "WARN", "error": "ERR ", "success": "OK  "}.get(level, "INFO")
        prefix = f"[{stamp}] {tag} "
        if level == "info":
            self.appendPlainText(prefix + message)
        else:
            color = self.COLORS[level]
            safe = html.escape(prefix + message).replace("\n", "<br>")
            self.appendHtml(f'<span style="color:{color}; white-space:pre-wrap">{safe}</span>')
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class DropTable(QTableWidget):
    """Table that accepts .dll/.zip files dropped from Explorer."""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTableWidget.DragDropMode.DropOnly)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)

    @staticmethod
    def _supported(event: QDragEnterEvent | QDropEvent) -> list[str]:
        paths: list[str] = []
        if not event.mimeData().hasUrls():
            return paths
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.casefold() in (".dll", ".zip"):
                paths.append(str(path))
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if self._supported(event) and self.isEnabled():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if self._supported(event) and self.isEnabled():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        paths = self._supported(event)
        if paths and self.isEnabled():
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
        else:
            event.ignore()


class LabeledValue(QWidget):
    """Small caption + value stacked vertically."""

    def __init__(self, caption: str, value: str = "—", parent=None) -> None:
        super().__init__(parent)
        self.caption = QLabel(caption)
        self.caption.setObjectName("Muted")
        self.value = QLabel(value)
        self.value.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
