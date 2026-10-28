"""Background task wrapper so network/filesystem work never blocks the UI."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

LOGGER = logging.getLogger(__name__)


class Task(QThread):
    """Runs ``fn(log)`` in a worker thread and reports back through signals.

    Only Qt signals are used for communication with the UI thread. Exceptions
    are converted into one readable message; the full traceback goes to the log
    file, never to the dialog.
    """

    message = Signal(str, str)  # level, text
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D401 - QThread API
        def log(text: str) -> None:
            self.message.emit("info", str(text))

        try:
            result = self._fn(log)
        except Exception as exc:  # noqa: BLE001 - boundary between core and UI
            LOGGER.error("Background task failed: %s", exc)
            LOGGER.debug(traceback.format_exc())
            text = str(exc).strip() or exc.__class__.__name__
            self.failed.emit(text)
            return
        self.succeeded.emit(result)
