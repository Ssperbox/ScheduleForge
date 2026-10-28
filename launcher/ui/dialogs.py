"""Small task-specific dialogs."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from launcher.core.models import ResolvedPackage
from launcher.ui.i18n import Translator


class DependencyDialog(QDialog):
    """Shows exactly what will be downloaded/installed before doing it."""

    def __init__(
        self,
        parent: QWidget,
        tr: Translator,
        package_name: str,
        resolved: list[ResolvedPackage],
        missing: list[str],
        notes: list[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr.t("ts.install_confirm_title", name=package_name))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(tr.t("ts.install_confirm_text"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        listing = QListWidget()
        listing.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        if resolved:
            for item in resolved:
                label = f"{item.package.full_name} {item.version.version_number}"
                if item.requested_by:
                    label += f"   ·   required by {item.requested_by}"
                entry = QListWidgetItem(label)
                entry.setToolTip(item.version.description)
                listing.addItem(entry)
        else:
            listing.addItem(QListWidgetItem(tr.t("ts.no_dependencies")))
        listing.setMinimumHeight(140)
        layout.addWidget(listing)

        if missing:
            title = QLabel(tr.t("ts.missing_dependencies"))
            title.setObjectName("Warning")
            title.setWordWrap(True)
            layout.addWidget(title)
            missing_list = QListWidget()
            missing_list.setMaximumHeight(90)
            for entry in missing:
                missing_list.addItem(QListWidgetItem(entry))
            layout.addWidget(missing_list)

        if notes:
            note_title = QLabel(tr.t("ts.loader_notes"))
            note_title.setObjectName("Muted")
            layout.addWidget(note_title)
            for note in notes:
                label = QLabel("• " + note)
                label.setWordWrap(True)
                label.setObjectName("Muted")
                layout.addWidget(label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr.t("button.install"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr.t("button.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class UninstallLoaderDialog(QDialog):
    """Confirmation with the optional removal of mod folders."""

    def __init__(self, parent: QWidget, tr: Translator, game_path: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr.t("dialog.uninstall_title"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        text = QLabel(tr.t("dialog.uninstall_text", path=game_path))
        text.setWordWrap(True)
        layout.addWidget(text)

        self.remove_mods = QCheckBox(tr.t("dialog.uninstall_remove_mods"))
        self.remove_mods.setChecked(False)
        layout.addWidget(self.remove_mods)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr.t("button.uninstall_loader"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr.t("button.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def wants_mod_removal(self) -> bool:
        return self.remove_mods.isChecked()
