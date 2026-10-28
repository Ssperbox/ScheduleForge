"""Entry point for the Schedule I Mod Launcher.

Run with:  py main.py
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from launcher.core.config import setup_file_logging
from launcher.core.models import APP_NAME, APP_VERSION
from launcher.ui.main_window import MainWindow

LOGGER = logging.getLogger(__name__)
ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"


def _exception_hook(exc_type, exc_value, exc_traceback) -> None:
    """Show unexpected errors instead of silently closing the app."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    LOGGER.critical("Unhandled exception\n%s", text)
    if QApplication.instance() is not None:
        QMessageBox.critical(
            None,
            "Unexpected error",
            f"{exc_value}\n\nThe details were written to the launcher log.",
        )
    else:
        sys.stderr.write(text)


def main() -> int:
    log_path = setup_file_logging()
    sys.excepthook = _exception_hook

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(APP_VERSION)
    QCoreApplication.setOrganizationName("ScheduleIModLauncher")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    LOGGER.info("Launcher started; log file: %s", log_path)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
