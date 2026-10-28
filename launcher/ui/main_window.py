"""Main application window.

The UI is deliberately thin: all game/loader/mod logic lives in
``launcher.core``. Long operations run in :class:`Task` threads.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "ru": "Русский",
    "zh": "中文",
    "ja": "日本語",
}

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from launcher.core import backup, game, loader, mod_mgr, saves, thunderstore
from launcher.core.config import ConfigStore, app_data_dir, backups_dir, logs_dir
from launcher.core.launch import LaunchError, launch_game
from launcher.core.models import (
    APP_NAME,
    APP_VERSION,
    STEAM_APP_ID,
    BackupInfo,
    GameInstall,
    ImportReport,
    ModFile,
    Profile,
    ResolvedPackage,
    SUPPORTED_THEMES,
    ThunderstorePackage,
)
from launcher.core.platform_win import normalize_path_key, open_in_file_manager
from launcher.core import steam_detect
from launcher.ui.dialogs import DependencyDialog, UninstallLoaderDialog
from launcher.ui.i18n import Translator
from launcher.ui.theme import apply_theme
from launcher.ui.widgets import Badge, Card, DropTable, LogView
from launcher.ui.workers import Task

LOGGER = logging.getLogger(__name__)


def shorten_path(text: str, limit: int = 88) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2 - 2]
    tail = text[-(limit // 2 - 1) :]
    return f"{head}…{tail}"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore | None = None) -> None:
        super().__init__()
        self.logger = logging.getLogger("launcher.ui")
        self.store = store or ConfigStore()
        self.config = self.store.load()
        self.tr = Translator(self.config.language)
        self.client = thunderstore.ThunderstoreClient()

        self.game: GameInstall | None = None
        self.mods: list[ModFile] = []
        self.backups: list[BackupInfo] = []
        self.save_slots: list[saves.SaveSlot] = []
        self.ts_results: list[ThunderstorePackage] = []
        self._pending_package: ThunderstorePackage | None = None

        self._tasks: set[Task] = set()
        self._updating_mods = False
        self._updating_profiles = False
        self._updating_settings = False
        self._game_running = False
        self._launch_deadline = 0.0
        self._config_error_shown = False

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(6000)
        self._play_timer.timeout.connect(self._poll_game_state)

        self._build_ui()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self.config.theme)
        self._retranslate()
        self._refresh_all()
        QTimer.singleShot(120, self._startup)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 880)
        self.setMinimumSize(980, 700)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_info_card())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_mods_tab(), "Mods")
        self.tabs.addTab(self._build_thunderstore_tab(), "Get mods")
        self.tabs.addTab(self._build_profiles_tab(), "Profiles")
        self.tabs.addTab(self._build_saves_tab(), "Saves")
        self.tabs.addTab(self._build_backups_tab(), "Backups")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self._build_log_panel())

        self.setCentralWidget(central)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(130)
        self.progress.setTextVisible(False)
        self.progress.hide()
        status.addWidget(self.status_label, 1)
        status.addPermanentWidget(self.progress)

    def _build_header(self) -> Card:
        card = Card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(3)
        self.title_label = QLabel()
        self.title_label.setObjectName("Title")
        self.path_label = QLabel()
        self.path_label.setObjectName("Muted")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("Subtitle")
        left.addWidget(self.title_label)
        left.addWidget(self.path_label)
        left.addWidget(self.subtitle_label)
        layout.addLayout(left, 1)

        self.detect_btn = QPushButton()
        self.browse_btn = QPushButton()
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("Primary")
        self.play_btn.setMinimumWidth(120)
        layout.addWidget(self.detect_btn)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.play_btn)

        self.detect_btn.clicked.connect(self._autodetect)
        self.browse_btn.clicked.connect(self._browse)
        self.play_btn.clicked.connect(self._play)
        return card

    def _build_info_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.badge_backend = Badge()
        self.badge_loader = Badge()
        self.badge_running = Badge()
        self.badge_saves = Badge()
        for badge in (self.badge_backend, self.badge_loader, self.badge_running, self.badge_saves):
            row.addWidget(badge)
        row.addStretch(1)

        self.install_loader_btn = QToolButton()
        self.install_loader_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.loader_menu = QMenu(self)
        self.loader_download_action = self.loader_menu.addAction("")
        self.loader_local_action = self.loader_menu.addAction("")
        self.loader_menu.triggered.connect(self._on_loader_menu)
        self.install_loader_btn.setMenu(self.loader_menu)
        self.install_loader_btn.clicked.connect(self._install_loader_latest)
        self.uninstall_loader_btn = QPushButton()
        self.uninstall_loader_btn.setObjectName("Danger")
        self.uninstall_loader_btn.clicked.connect(self._uninstall_loader)
        row.addWidget(self.install_loader_btn)
        row.addWidget(self.uninstall_loader_btn)
        layout.addLayout(row)

        bottom = QHBoxLayout()
        self.details_label = QLabel()
        self.details_label.setObjectName("Muted")
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_game_btn = QPushButton()
        self.open_game_btn.setObjectName("Flat")
        self.open_game_btn.clicked.connect(self._open_game_folder)
        bottom.addWidget(self.details_label, 1)
        bottom.addWidget(self.open_game_btn)
        layout.addLayout(bottom)

        self.warning_label = QLabel()
        self.warning_label.setObjectName("Warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)
        return card

    def _build_mods_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.add_mod_btn = QPushButton()
        self.remove_mod_btn = QPushButton()
        self.remove_mod_btn.setObjectName("Danger")
        self.refresh_mods_btn = QPushButton()
        self.open_mods_btn = QPushButton()
        self.check_updates_btn = QPushButton()
        self.loader_toggle_btn = QPushButton()
        top.addWidget(self.add_mod_btn)
        top.addWidget(self.remove_mod_btn)
        top.addWidget(self.refresh_mods_btn)
        top.addWidget(self.open_mods_btn)
        top.addWidget(self.check_updates_btn)
        top.addWidget(self.loader_toggle_btn)
        top.addStretch(1)
        self.profile_caption = QLabel()
        self.profile_caption.setObjectName("Muted")
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(170)
        self.profile_combo.currentTextChanged.connect(self._on_profile_combo)
        top.addWidget(self.profile_caption)
        top.addWidget(self.profile_combo)
        layout.addLayout(top)

        self.mods_table = DropTable()
        self.mods_table.setColumnCount(6)
        self.mods_table.setSortingEnabled(False)
        header = self.mods_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.mods_table.setColumnWidth(0, 52)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.itemChanged.connect(self._on_mod_item_changed)
        self.mods_table.itemSelectionChanged.connect(self._update_action_states)
        self.mods_table.files_dropped.connect(self._import_files)
        layout.addWidget(self.mods_table, 1)

        self.mods_hint = QLabel()
        self.mods_hint.setObjectName("Muted")
        self.mods_hint.setWordWrap(True)
        layout.addWidget(self.mods_hint)

        self.add_mod_btn.clicked.connect(self._add_mods)
        self.remove_mod_btn.clicked.connect(self._remove_selected_mod)
        self.refresh_mods_btn.clicked.connect(self._refresh_game_state)
        self.open_mods_btn.clicked.connect(self._open_mods_folder)
        self.check_updates_btn.clicked.connect(self._check_mod_updates)
        self.loader_toggle_btn.clicked.connect(self._toggle_loader)
        return tab

    def _build_thunderstore_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        self.ts_search = QLineEdit()
        self.ts_search.setClearButtonEnabled(True)
        self.ts_search.returnPressed.connect(self._search_thunderstore)
        self.ts_search_btn = QPushButton()
        self.ts_refresh_btn = QPushButton()
        search_row.addWidget(self.ts_search, 1)
        search_row.addWidget(self.ts_search_btn)
        search_row.addWidget(self.ts_refresh_btn)
        layout.addLayout(search_row)

        self.ts_status = QLabel()
        self.ts_status.setObjectName("Muted")
        self.ts_status.setWordWrap(True)
        layout.addWidget(self.ts_status)

        self.ts_table = QTableWidget()
        self.ts_table.setColumnCount(5)
        self.ts_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ts_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.ts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ts_table.verticalHeader().setVisible(False)
        self.ts_table.setAlternatingRowColors(True)
        ts_header = self.ts_table.horizontalHeader()
        ts_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            ts_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        ts_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.ts_table.itemSelectionChanged.connect(self._update_action_states)
        self.ts_table.cellDoubleClicked.connect(lambda *_: self._install_thunderstore())
        layout.addWidget(self.ts_table, 1)

        bottom = QHBoxLayout()
        self.ts_note = QLabel()
        self.ts_note.setObjectName("Muted")
        self.ts_note.setWordWrap(True)
        self.ts_install_btn = QPushButton()
        self.ts_install_btn.setObjectName("Primary")
        bottom.addWidget(self.ts_note, 1)
        bottom.addWidget(self.ts_install_btn)
        layout.addLayout(bottom)

        self.ts_search_btn.clicked.connect(self._search_thunderstore)
        self.ts_refresh_btn.clicked.connect(self._refresh_thunderstore_index)
        self.ts_install_btn.clicked.connect(self._install_thunderstore)
        return tab

    def _build_profiles_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.profiles_explain = QLabel()
        self.profiles_explain.setObjectName("Muted")
        self.profiles_explain.setWordWrap(True)
        layout.addWidget(self.profiles_explain)

        self.profiles_active_label = QLabel()
        layout.addWidget(self.profiles_active_label)

        body = QHBoxLayout()
        self.profiles_table = QTableWidget()
        self.profiles_table.setColumnCount(2)
        self.profiles_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.profiles_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.profiles_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.profiles_table.verticalHeader().setVisible(False)
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.profiles_table.itemSelectionChanged.connect(self._update_action_states)
        self.profiles_table.cellDoubleClicked.connect(lambda *_: self._activate_selected_profile())
        body.addWidget(self.profiles_table, 1)

        buttons = QVBoxLayout()
        self.profile_activate_btn = QPushButton()
        self.profile_new_btn = QPushButton()
        self.profile_duplicate_btn = QPushButton()
        self.profile_rename_btn = QPushButton()
        self.profile_delete_btn = QPushButton()
        self.profile_delete_btn.setObjectName("Danger")
        self.profile_export_btn = QPushButton()
        self.profile_import_btn = QPushButton()
        for button in (
            self.profile_activate_btn,
            self.profile_new_btn,
            self.profile_duplicate_btn,
            self.profile_rename_btn,
            self.profile_delete_btn,
            self.profile_export_btn,
            self.profile_import_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        body.addLayout(buttons)
        layout.addLayout(body, 1)

        self.profile_activate_btn.clicked.connect(self._activate_selected_profile)
        self.profile_new_btn.clicked.connect(self._new_profile)
        self.profile_duplicate_btn.clicked.connect(self._duplicate_profile)
        self.profile_rename_btn.clicked.connect(self._rename_profile)
        self.profile_delete_btn.clicked.connect(self._delete_profile)
        self.profile_export_btn.clicked.connect(self._export_profile)
        self.profile_import_btn.clicked.connect(self._import_profile)
        return tab

    def _build_saves_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.saves_explain = QLabel()
        self.saves_explain.setObjectName("Muted")
        self.saves_explain.setWordWrap(True)
        layout.addWidget(self.saves_explain)

        body = QHBoxLayout()
        self.saves_table = QTableWidget()
        self.saves_table.setColumnCount(4)
        self.saves_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.saves_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.saves_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.saves_table.verticalHeader().setVisible(False)
        self.saves_table.setAlternatingRowColors(True)
        header = self.saves_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.saves_table.itemSelectionChanged.connect(self._update_action_states)
        body.addWidget(self.saves_table, 1)

        buttons = QVBoxLayout()
        self.save_rename_btn = QPushButton()
        self.save_link_btn = QPushButton()
        self.save_unlink_btn = QPushButton()
        self.save_delete_btn = QPushButton()
        self.save_delete_btn.setObjectName("Danger")
        for button in (
            self.save_rename_btn,
            self.save_link_btn,
            self.save_unlink_btn,
            self.save_delete_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        body.addLayout(buttons)
        layout.addLayout(body, 1)

        self.save_rename_btn.clicked.connect(self._rename_save)
        self.save_link_btn.clicked.connect(self._link_save_to_active)
        self.save_unlink_btn.clicked.connect(self._unlink_save)
        self.save_delete_btn.clicked.connect(self._delete_save)
        return tab

    def _build_backups_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.backups_explain = QLabel()
        self.backups_explain.setObjectName("Muted")
        self.backups_explain.setWordWrap(True)
        layout.addWidget(self.backups_explain)

        row = QHBoxLayout()
        self.backup_now_btn = QPushButton()
        self.backup_restore_btn = QPushButton()
        self.backup_delete_btn = QPushButton()
        self.backup_open_btn = QPushButton()
        self.backup_saves_btn = QPushButton()
        self.backup_delete_btn.setObjectName("Danger")
        row.addWidget(self.backup_now_btn)
        row.addWidget(self.backup_restore_btn)
        row.addWidget(self.backup_delete_btn)
        row.addWidget(self.backup_open_btn)
        row.addStretch(1)
        row.addWidget(self.backup_saves_btn)
        layout.addLayout(row)

        self.backups_table = QTableWidget()
        self.backups_table.setColumnCount(4)
        self.backups_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.backups_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.backups_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.backups_table.verticalHeader().setVisible(False)
        self.backups_table.setAlternatingRowColors(True)
        backup_header = self.backups_table.horizontalHeader()
        backup_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        backup_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        backup_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        backup_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.backups_table.itemSelectionChanged.connect(self._update_action_states)
        layout.addWidget(self.backups_table, 1)

        self.backups_path_label = QLabel()
        self.backups_path_label.setObjectName("Muted")
        self.backups_path_label.setWordWrap(True)
        layout.addWidget(self.backups_path_label)

        self.backup_now_btn.clicked.connect(self._create_backup_now)
        self.backup_restore_btn.clicked.connect(self._restore_selected_backup)
        self.backup_delete_btn.clicked.connect(self._delete_selected_backup)
        self.backup_open_btn.clicked.connect(lambda: self._open_path(backup.backups_path()))
        self.backup_saves_btn.clicked.connect(self._backup_saves)
        return tab

    def _build_settings_tab(self) -> QWidget:
        # Settings is taller than the other tabs. Put it in a scroll area so a
        # short window scrolls it instead of squeezing combos/group boxes until
        # their text is clipped.
        tab = QScrollArea()
        tab.setObjectName("SettingsScroll")
        tab.setWidgetResizable(True)
        tab.setFrameShape(QFrame.Shape.NoFrame)
        tab.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        # Keep the content at its real minimum height; the scroll area then
        # shows a scrollbar rather than compressing children.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        self.settings_labels: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)

        def row(key: str, widget: QWidget) -> None:
            label = QLabel()
            label.setObjectName("Muted")
            self.settings_labels[key] = label
            form.addRow(label, widget)

        self.launch_combo = QComboBox()
        self.args_edit = QLineEdit()
        self.language_combo = QComboBox()
        self.theme_combo = QComboBox()
        for field in (self.launch_combo, self.args_edit, self.language_combo, self.theme_combo):
            field.setMinimumHeight(32)
        row("settings.launch_method", self.launch_combo)
        row("settings.extra_args", self.args_edit)
        row("settings.language", self.language_combo)
        row("settings.theme", self.theme_combo)
        layout.addLayout(form)
        self.args_hint = QLabel()
        self.args_hint.setObjectName("Muted")
        self.args_hint.setWordWrap(True)
        layout.addWidget(self.args_hint)

        self.data_group = QGroupBox()
        data_layout = QVBoxLayout(self.data_group)
        self.settings_data_label = QLabel()
        self.settings_data_label.setObjectName("Muted")
        self.settings_data_label.setWordWrap(True)
        self.settings_data_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        data_layout.addWidget(self.settings_data_label)
        data_buttons = QHBoxLayout()
        self.open_config_btn = QPushButton()
        self.open_logs_btn = QPushButton()
        data_buttons.addWidget(self.open_config_btn)
        data_buttons.addWidget(self.open_logs_btn)
        data_buttons.addStretch(1)
        data_layout.addLayout(data_buttons)
        layout.addWidget(self.data_group)

        self.privacy_group = QGroupBox()
        privacy_layout = QVBoxLayout(self.privacy_group)
        self.privacy_label = QLabel()
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setObjectName("Muted")
        privacy_layout.addWidget(self.privacy_label)
        layout.addWidget(self.privacy_group)

        self.about_group = QGroupBox()
        about_layout = QVBoxLayout(self.about_group)
        self.about_label = QLabel()
        self.about_label.setWordWrap(True)
        self.about_label.setObjectName("Muted")
        about_layout.addWidget(self.about_label)
        layout.addWidget(self.about_group)
        layout.addStretch(1)

        self.launch_combo.currentIndexChanged.connect(self._on_launch_method_changed)
        self.args_edit.editingFinished.connect(self._on_args_changed)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.open_config_btn.clicked.connect(lambda: self._open_path(app_data_dir()))
        self.open_logs_btn.clicked.connect(lambda: self._open_path(logs_dir()))

        tab.setWidget(content)
        return tab

    def _build_log_panel(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(5)
        row = QHBoxLayout()
        self.log_title = QLabel()
        self.log_title.setObjectName("SectionTitle")
        self.log_clear_btn = QPushButton()
        self.log_clear_btn.setObjectName("Flat")
        self.log_copy_btn = QPushButton()
        self.log_copy_btn.setObjectName("Flat")
        row.addWidget(self.log_title)
        row.addStretch(1)
        row.addWidget(self.log_copy_btn)
        row.addWidget(self.log_clear_btn)
        layout.addLayout(row)
        self.log_view = LogView()
        self.log_view.setMaximumHeight(170)
        layout.addWidget(self.log_view)
        self.log_clear_btn.clicked.connect(self.log_view.clear)
        self.log_copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.log_view.toPlainText())
        )
        return card

    # ------------------------------------------------------------------
    # Translation / theme
    # ------------------------------------------------------------------

    def t(self, key: str, **values) -> str:
        return self.tr.t(key, **values)

    def _retranslate(self) -> None:
        self.setWindowTitle(self.t("app.title"))
        self.title_label.setText(self.t("app.title"))
        self.subtitle_label.setText(f"{self.t('app.subtitle')} · {self.t('app.unofficial')}")
        self.detect_btn.setText(self.t("button.auto_detect"))
        self.browse_btn.setText(self.t("button.browse"))
        self.play_btn.setText(self.t("button.stop" if self._game_running else "button.play"))
        self.install_loader_btn.setText(self.t("button.install_loader"))
        self.loader_download_action.setText(self.t("button.loader_download"))
        self.loader_local_action.setText(self.t("button.loader_from_file"))
        self.uninstall_loader_btn.setText(self.t("button.uninstall_loader"))
        self.open_game_btn.setText(self.t("button.open_folder"))

        self.tabs.setTabText(0, self.t("tab.mods"))
        self.tabs.setTabText(1, self.t("tab.browse"))
        self.tabs.setTabText(2, self.t("tab.profiles"))
        self.tabs.setTabText(3, self.t("tab.saves"))
        self.tabs.setTabText(4, self.t("tab.backups"))
        self.tabs.setTabText(5, self.t("tab.settings"))

        self.mods_table.setHorizontalHeaderLabels(
            [
                self.t("mods.col.on"),
                self.t("mods.col.name"),
                self.t("mods.col.version"),
                self.t("mods.col.author"),
                self.t("mods.col.type"),
                self.t("mods.col.status"),
            ]
        )
        self.add_mod_btn.setText(self.t("button.add_mod"))
        self.remove_mod_btn.setText(self.t("button.remove_mod"))
        self.refresh_mods_btn.setText(self.t("button.refresh"))
        self.open_mods_btn.setText(self.t("button.open_folder"))
        self.check_updates_btn.setText(self.t("mods.check_updates"))
        self._refresh_loader_toggle_text()
        self.profile_caption.setText(self.t("mods.profile"))
        self.saves_explain.setText(self.t("saves.explain"))
        self.save_rename_btn.setText(self.t("saves.rename"))
        self.save_link_btn.setText(self.t("saves.link"))
        self.save_unlink_btn.setText(self.t("saves.unlink"))
        self.save_delete_btn.setText(self.t("saves.delete"))
        self.saves_table.setHorizontalHeaderLabels(
            [
                self.t("saves.col.name"),
                self.t("saves.col.modified"),
                self.t("saves.col.size"),
                self.t("saves.col.profiles"),
            ]
        )

        self.ts_search.setPlaceholderText(self.t("ts.search_placeholder"))
        self.ts_search_btn.setText(self.t("button.search"))
        self.ts_refresh_btn.setText(self.t("ts.refresh_index"))
        self.ts_install_btn.setText(self.t("verb.install"))
        self.ts_note.setText(self.t("ts.note"))
        self.ts_table.setHorizontalHeaderLabels(
            [
                self.t("ts.col.name"),
                self.t("ts.col.author"),
                self.t("ts.col.version"),
                self.t("ts.col.downloads"),
                self.t("ts.col.description"),
            ]
        )

        self.profiles_explain.setText(self.t("profiles.explain"))
        self.profiles_table.setHorizontalHeaderLabels(
            [self.t("profiles.col.name"), self.t("profiles.col.mods")]
        )
        self.profile_activate_btn.setText(self.t("profiles.activate"))
        self.profile_new_btn.setText(self.t("button.create"))
        self.profile_duplicate_btn.setText(self.t("profiles.duplicate_title"))
        self.profile_rename_btn.setText(self.t("profiles.rename_title"))
        self.profile_delete_btn.setText(self.t("button.delete"))
        self.profile_export_btn.setText(self.t("profiles.export"))
        self.profile_import_btn.setText(self.t("profiles.import"))

        self.backups_explain.setText(self.t("backups.explain"))
        self.backups_table.setHorizontalHeaderLabels(
            [
                self.t("backups.col.date"),
                self.t("backups.col.label"),
                self.t("backups.col.type"),
                self.t("backups.col.size"),
            ]
        )
        self.backup_now_btn.setText(self.t("button.backup_now"))
        self.backup_restore_btn.setText(self.t("button.restore"))
        self.backup_delete_btn.setText(self.t("button.delete"))
        self.backup_open_btn.setText(self.t("button.open_folder"))
        self.backup_saves_btn.setText(self.t("button.backup_saves"))
        self.backups_path_label.setText(str(backups_dir()))

        for key, label in self.settings_labels.items():
            label.setText(self.t(key))
        self.args_hint.setText(self.t("settings.extra_args_hint"))
        self.data_group.setTitle(self.t("settings.data"))
        self.privacy_group.setTitle(self.t("settings.privacy"))
        self.about_group.setTitle(self.t("settings.about"))
        self.privacy_label.setText(self.t("settings.privacy_text"))
        self.about_label.setText(self.t("settings.about_text", version=APP_VERSION))
        self.open_config_btn.setText(self.t("settings.open_config"))
        self.open_logs_btn.setText(self.t("settings.open_logs"))
        self.settings_data_label.setText(
            self.t(
                "settings.data_text",
                config=self.store.path,
                backups=backups_dir(),
                logs=logs_dir(),
            )
        )

        self.log_title.setText(self.t("log.title"))
        self.log_clear_btn.setText(self.t("button.clear"))
        self.log_copy_btn.setText(self.t("button.copy"))

        self._fill_settings_combos()
        self._refresh_all()

    def _fill_settings_combos(self) -> None:
        self._updating_settings = True
        try:
            current = self.config.launch_method
            self.launch_combo.clear()
            self.launch_combo.addItem(self.t("settings.launch_steam"), "steam")
            self.launch_combo.addItem(self.t("settings.launch_direct"), "direct")
            index = self.launch_combo.findData(current)
            self.launch_combo.setCurrentIndex(max(index, 0))

            self.language_combo.clear()
            for code, name in LANGUAGE_NAMES.items():
                self.language_combo.addItem(name, code)
            index = self.language_combo.findData(self.config.language)
            self.language_combo.setCurrentIndex(max(index, 0))

            self.theme_combo.clear()
            for theme_key in SUPPORTED_THEMES:
                self.theme_combo.addItem(self.t(f"settings.theme_{theme_key}"), theme_key)
            index = self.theme_combo.findData(self.config.theme)
            self.theme_combo.setCurrentIndex(max(index, 0))

            self.args_edit.setText(self.config.extra_args)
        finally:
            self._updating_settings = False

    # ------------------------------------------------------------------
    # Startup / game selection
    # ------------------------------------------------------------------

    def _startup(self) -> None:
        self._append_log("info", self.t("log.config_loaded", path=self.store.path))
        for warning in self.store.load_warnings:
            self._append_log("warn", warning)
        loaded = False
        if self.config.game_path:
            loaded = self._load_game(Path(self.config.game_path), interactive=False)

        if not loaded:
            installations = game.detect_installations()
            if installations:
                selected = installations[0]
                self._append_log(
                    "info", self.t("log.detected", path=str(selected.root))
                )
                self._load_game(selected.root, interactive=False)
            else:
                self._append_log("warn", self.t("log.detect_failed"))
                self._append_log("info", self.t("log.no_game"))
        self._refresh_all()

    def _steam_app_for(self, path: Path):
        try:
            key = normalize_path_key(path)
            for app in steam_detect.find_app():
                if normalize_path_key(app.install_dir) == key:
                    return app
        except Exception:  # noqa: BLE001 - detection must never break the UI
            LOGGER.debug("Steam manifest lookup failed", exc_info=True)
        return None

    def _load_game(self, path: Path, *, interactive: bool) -> bool:
        validation = game.validate_game_dir(path)
        if not validation.ok:
            self._append_log("error", f"{self.t('log.path_invalid')} {validation.message}")
            if interactive:
                QMessageBox.warning(self, self.t("dialog.invalid_game"), validation.message)
            return False
        try:
            install = game.inspect_game(path)
        except Exception as exc:  # noqa: BLE001
            self._append_log("error", f"Could not inspect the game folder: {exc}")
            if interactive:
                QMessageBox.warning(self, self.t("dialog.invalid_game"), str(exc))
            return False
        steam_app = self._steam_app_for(install.root)
        if steam_app is not None:
            install = game.inspect_game(install.root, steam_app)
        self.game = install
        self.config.game_path = str(install.root)
        self._save_config()
        self._game_running = game.is_game_running()
        if self._game_running:
            self._launch_deadline = 0.0
            self._play_timer.start()
        self._append_log("success", self.t("log.game_loaded", path=install.root))
        self._refresh_all()
        return True

    def _browse(self) -> None:
        start = self.config.game_path or str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self, self.t("header.game_folder"), start
        )
        if not directory:
            return
        if self._load_game(Path(directory), interactive=True):
            self._append_log("info", self.t("log.path_saved"))

    def _autodetect(self) -> None:
        if self._tasks:
            self._append_log("warn", self.t("log.busy"))
            return
        self._append_log("info", self.t("log.scanning"))
        installations = game.detect_installations()
        if not installations:
            self._append_log("warn", self.t("log.detect_failed"))
            QMessageBox.information(
                self, self.t("dialog.no_game_title"), self.t("log.detect_failed")
            )
            return
        chosen = installations[0]
        if len(installations) > 1:
            labels = [str(item.root) for item in installations]
            label, accepted = QInputDialog.getItem(
                self,
                self.t("dialog.select_game"),
                self.t("header.game_folder"),
                labels,
                0,
                False,
            )
            if not accepted:
                return
            chosen = installations[labels.index(label)]
        self._load_game(chosen.root, interactive=True)
        self._append_log("info", self.t("log.detected", path=chosen.root))

    def _require_game(self) -> bool:
        if self.game is None or not self.game.valid:
            QMessageBox.information(
                self, self.t("dialog.no_game_title"), self.t("dialog.no_game_text")
            )
            return False
        return True

    def _require_not_running(self) -> bool:
        if self._game_running or game.is_game_running():
            self._game_running = True
            self._append_log("warn", self.t("log.game_running_block"))
            if not self._play_timer.isActive():
                self._launch_deadline = 0.0
                self._play_timer.start()
            QMessageBox.warning(
                self, self.t("dialog.game_running_title"), self.t("dialog.game_running_text")
            )
            self._refresh_header()
            self._update_action_states()
            return False
        return True

    # ------------------------------------------------------------------
    # Refresh / status
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        try:
            self.store.save(self.config)
            self._config_error_shown = False
        except OSError as exc:
            self._append_log("error", self.t("log.config_error", error=exc))
            if not self._config_error_shown:
                self._config_error_shown = True
                QMessageBox.warning(self, self.t("dialog.error"), str(exc))

    def _refresh_all(self) -> None:
        self._refresh_header()
        self._refresh_mods()
        self._refresh_profiles()
        self._refresh_saves()
        self._refresh_backups()
        self._update_action_states()

    def _refresh_header(self) -> None:
        if self.game is None:
            self.path_label.setText(self.t("header.no_game"))
            self.path_label.setToolTip("")
            self.details_label.setText("—")
            self.warning_label.hide()
            self.badge_backend.setText(self.t("badge.unknown_backend"))
            self.badge_backend.set_state("neutral")
            self.badge_loader.setText(self.t("badge.no_loader"))
            self.badge_loader.set_state("neutral")
            self.badge_running.setText(self.t("badge.game_stopped"))
            self.badge_running.set_state("neutral")
            self.badge_saves.setText(self.t("badge.saves_none"))
            self.badge_saves.set_state("neutral")
            self.open_game_btn.setEnabled(False)
            return

        install = self.game
        self.path_label.setText(shorten_path(str(install.root)))
        self.path_label.setToolTip(str(install.root))
        self.open_game_btn.setEnabled(True)

        backend_text = {
            "il2cpp": self.t("badge.il2cpp"),
            "mono": self.t("badge.mono"),
        }.get(install.backend.value, self.t("badge.unknown_backend"))
        self.badge_backend.setText(backend_text)
        self.badge_backend.set_state(
            "warn" if install.backend.value in ("mono", "unknown") else "neutral"
        )
        self.badge_backend.setToolTip("\n".join(install.warnings))

        if install.loader.installed:
            text = (
                self.t("badge.loader", version=install.loader.version)
                if install.loader.version
                else self.t("badge.loader_unknown")
            )
            self.badge_loader.setText(text)
            self.badge_loader.set_state("warn" if install.loader.warnings else "ok")
            self.badge_loader.setToolTip(
                "\n".join(install.loader.warnings)
                or (f"Proxy: {install.loader.proxy_dll}" if install.loader.proxy_dll else "")
            )
        else:
            self.badge_loader.setText(self.t("badge.no_loader"))
            self.badge_loader.set_state("warn")
            self.badge_loader.setToolTip("")

        self.badge_running.setText(
            self.t("badge.game_running") if self._game_running else self.t("badge.game_stopped")
        )
        self.badge_running.set_state("ok" if self._game_running else "neutral")

        saves_exist = bool(install.save_dir and install.save_dir.is_dir())
        self.badge_saves.setText(
            self.t("badge.saves_ok") if saves_exist else self.t("badge.saves_none")
        )
        self.badge_saves.set_state("ok" if saves_exist else "neutral")
        if install.save_dir:
            self.badge_saves.setToolTip(str(install.save_dir))

        build = install.build_id or "—"
        unity = install.unity_version or "—"
        saves_text = (
            str(install.save_dir) if saves_exist else self.t("header.saves_missing")
        )
        self.details_label.setText(
            self.t("header.details", build=build, unity=unity, save_path=saves_text)
        )
        self.details_label.setToolTip(
            f"AppID {STEAM_APP_ID} · Library: {install.steam_library or '—'}"
        )

        if install.warnings:
            first = install.warnings[0]
            more = f" (+{len(install.warnings) - 1})" if len(install.warnings) > 1 else ""
            self.warning_label.setText("⚠  " + first + more)
            self.warning_label.setToolTip("\n".join(install.warnings))
            self.warning_label.show()
        else:
            self.warning_label.hide()

    def _refresh_mods(self) -> None:
        self._updating_mods = True
        try:
            self.mods_table.setRowCount(0)
            if self.game is None:
                self.mods = []
                self.mods_hint.setText(self.t("mods.empty"))
                return
            try:
                self.mods = mod_mgr.scan(self.game.root)
            except Exception as exc:  # noqa: BLE001
                self.mods = []
                self._append_log("error", f"Could not scan mods: {exc}")
            self.mods_table.setRowCount(len(self.mods))
            for row, mod in enumerate(self.mods):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked
                )
                self.mods_table.setItem(row, 0, check)

                name_item = QTableWidgetItem(mod.name)
                name_item.setData(Qt.ItemDataRole.UserRole, mod.mod_id)
                tooltip = mod.description or mod.rel
                if mod.dependencies:
                    tooltip += "\n" + self.t(
                        "mods.dependencies", deps=", ".join(mod.dependencies)
                    )
                name_item.setToolTip(tooltip)
                self.mods_table.setItem(row, 1, name_item)
                self.mods_table.setItem(row, 2, QTableWidgetItem(mod.display_version))
                self.mods_table.setItem(row, 3, QTableWidgetItem(mod.display_author))
                self.mods_table.setItem(
                    row, 4, QTableWidgetItem(self.t(f"mods.type.{mod.kind.value}"))
                )
                status = QTableWidgetItem()
                self.mods_table.setItem(row, 5, status)
                self._style_mod_row(row, mod.enabled)
                if self.game is not None and mod_mgr.backend_mismatch(
                    mod, self.game.backend.value
                ):
                    status.setText(self.t("mods.status.backend_mismatch"))
                    status.setToolTip(self.t("mods.backend_mismatch_tip"))
            self._refresh_mods_hint()
            self._refresh_loader_toggle_text()
        finally:
            self._updating_mods = False

    def _style_mod_row(self, row: int, enabled: bool) -> None:
        status_item = self.mods_table.item(row, 5)
        if status_item is None:
            return
        status_item.setText(
            self.t("mods.status.enabled") if enabled else self.t("mods.status.disabled")
        )
        muted = QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText))
        for column in range(6):
            item = self.mods_table.item(row, column)
            if item is not None:
                item.setForeground(QBrush() if enabled else muted)

    def _refresh_mods_hint(self) -> None:
        count = len(self.mods)
        if count == 0:
            self.mods_hint.setText(self.t("mods.empty"))
            return
        parts = [self.t("mods.count", count=count), self.t("mods.hint")]
        if self.game is not None and not self.game.loader.installed:
            parts.append(self.t("mods.requires_loader"))
        self.mods_hint.setText("  ·  ".join(parts))

    def _refresh_profiles(self) -> None:
        self._updating_profiles = True
        try:
            self.profile_combo.clear()
            for name in self.config.profiles:
                self.profile_combo.addItem(name)
            if self.config.active_profile in self.config.profiles:
                self.profile_combo.setCurrentText(self.config.active_profile)

            names = list(self.config.profiles)
            self.profiles_table.setRowCount(len(names))
            for row, name in enumerate(names):
                profile = self.config.profiles[name]
                enabled = sum(
                    1 for mod in self.mods if profile.is_enabled(mod.mod_id)
                )
                label = ("● " if name == self.config.active_profile else "") + name
                name_item = QTableWidgetItem(label)
                name_item.setData(Qt.ItemDataRole.UserRole, name)
                self.profiles_table.setItem(row, 0, name_item)
                self.profiles_table.setItem(
                    row,
                    1,
                    QTableWidgetItem(f"{enabled} / {len(self.mods)}"),
                )
            self.profiles_active_label.setText(
                self.t("profiles.active", name=self.config.active_profile)
            )
        finally:
            self._updating_profiles = False
        self._update_action_states()

    def _refresh_backups(self) -> None:
        try:
            self.backups = backup.list_backups()
        except Exception as exc:  # noqa: BLE001
            self.backups = []
            self._append_log("error", f"Could not list backups: {exc}")
        self.backups_table.setRowCount(len(self.backups))
        for row, info in enumerate(self.backups):
            date = info.created_at.replace("T", " ")[:16] if info.created_at else "—"
            date_item = QTableWidgetItem(date)
            date_item.setData(Qt.ItemDataRole.UserRole, info.id)
            self.backups_table.setItem(row, 0, date_item)
            self.backups_table.setItem(row, 1, QTableWidgetItem(info.label))
            self.backups_table.setItem(row, 2, QTableWidgetItem(self._kind_label(info.kind)))
            self.backups_table.setItem(
                row, 3, QTableWidgetItem(human_size(info.size_bytes))
            )
        self.backups_path_label.setText(str(backups_dir()))
        self._update_action_states()

    def _kind_label(self, kind: str) -> str:
        key = f"backups.kind.{kind}"
        translated = self.t(key)
        return translated if translated != key else kind

    def _update_action_states(self) -> None:
        busy = bool(self._tasks)
        has_game = self.game is not None and self.game.valid
        running = self._game_running
        self.progress.setVisible(busy)

        self.play_btn.setText(self.t("button.stop" if running else "button.play"))
        self.play_btn.setEnabled(has_game and (running or not busy))
        self.browse_btn.setEnabled(not busy)
        self.detect_btn.setEnabled(not busy)
        self.install_loader_btn.setEnabled(has_game and not busy)
        self.uninstall_loader_btn.setEnabled(
            has_game and not busy and bool(self.game and self.game.loader.installed)
        )
        self.add_mod_btn.setEnabled(has_game and not busy and not running)
        self.refresh_mods_btn.setEnabled(not busy)
        self.open_mods_btn.setEnabled(has_game)
        self.remove_mod_btn.setEnabled(
            has_game and not busy and not running and self._selected_mod() is not None
        )
        self.profile_combo.setEnabled(has_game and not busy and not running)
        self.profiles_table.setEnabled(not busy)
        self.profile_new_btn.setEnabled(not busy and has_game and not running)
        self.profile_duplicate_btn.setEnabled(
            not busy and has_game and not running and self._selected_profile() is not None
        )
        self.profile_rename_btn.setEnabled(
            not busy and has_game and not running and self._selected_profile() is not None
        )
        self.profile_delete_btn.setEnabled(
            not busy and has_game and not running and self._selected_profile() is not None
        )
        self.profile_activate_btn.setEnabled(
            not busy
            and has_game
            and not running
            and self._selected_profile() not in (None, self.config.active_profile)
        )
        self.ts_search_btn.setEnabled(not busy)
        self.ts_refresh_btn.setEnabled(not busy)
        self.ts_install_btn.setEnabled(
            not busy and has_game and self._selected_ts_package() is not None
        )
        self.backup_now_btn.setEnabled(has_game and not busy and not running)
        self.backup_saves_btn.setEnabled(not busy)
        self.backup_restore_btn.setEnabled(
            not busy and not running and self._selected_backup() is not None
        )
        self.backup_delete_btn.setEnabled(
            not busy and self._selected_backup() is not None
        )
        self.backup_open_btn.setEnabled(True)
        self.launch_combo.setEnabled(not busy)
        self.args_edit.setEnabled(not busy)
        self.language_combo.setEnabled(not busy)
        self.theme_combo.setEnabled(not busy)

        if busy:
            self.status_label.setText(self.t("status.working"))
        elif running:
            self.status_label.setText(self.t("status.running"))
        elif has_game:
            self.status_label.setText(self.t("status.ready"))
        else:
            self.status_label.setText(self.t("status.no_game"))

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    def _run_task(self, job, on_success, on_error=None) -> None:
        if self._tasks:
            self._append_log("warn", self.t("log.busy"))
            return
        task = Task(job, self)
        task.message.connect(self._append_log)
        task.succeeded.connect(
            lambda result: self._handle_task_success(task, result, on_success)
        )
        task.failed.connect(
            lambda message: self._handle_task_failure(task, message, on_error)
        )
        task.finished.connect(lambda: self._finish_task(task))
        self._tasks.add(task)
        self._update_action_states()
        task.start()

    def _handle_task_success(self, task: Task, result, on_success) -> None:
        try:
            on_success(result)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Post-task handler failed")
            self._append_log("error", str(exc))
            QMessageBox.critical(self, self.t("dialog.error"), str(exc))

    def _handle_task_failure(self, task: Task, message: str, on_error) -> None:
        self._append_log("error", message)
        handled = False
        if on_error is not None:
            try:
                handled = bool(on_error(message))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Task error handler failed")
        if not handled:
            QMessageBox.critical(self, self.t("dialog.error"), message)

    def _finish_task(self, task: Task) -> None:
        self._tasks.discard(task)
        task.deleteLater()
        self._update_action_states()
        self._refresh_all()

    def _append_log(self, level: str, message: str) -> None:
        text = str(message)
        if hasattr(self, "log_view"):
            self.log_view.append_line(level, text)
        if level == "error":
            self.logger.error(text)
        elif level == "warn":
            self.logger.warning(text)
        else:
            self.logger.info(text)

    # ------------------------------------------------------------------
    # Mods
    # ------------------------------------------------------------------

    def _selected_mod(self) -> ModFile | None:
        row = self.mods_table.currentRow()
        if 0 <= row < len(self.mods):
            return self.mods[row]
        return None

    def _set_mod_check(self, row: int, enabled: bool) -> None:
        self._updating_mods = True
        try:
            item = self.mods_table.item(row, 0)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
                )
        finally:
            self._updating_mods = False

    def _on_mod_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_mods or item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self.mods)) or self.game is None:
            return
        mod = self.mods[row]
        desired = item.checkState() == Qt.CheckState.Checked
        if desired == mod.enabled:
            return
        if self._tasks:
            self._append_log("warn", self.t("log.busy"))
            self._set_mod_check(row, mod.enabled)
            return
        if self._game_running or game.is_game_running():
            self._game_running = True
            self._append_log("warn", self.t("log.game_running_block"))
            if not self._play_timer.isActive():
                self._launch_deadline = 0.0
                self._play_timer.start()
            self._set_mod_check(row, mod.enabled)
            QMessageBox.warning(
                self, self.t("dialog.game_running_title"), self.t("dialog.game_running_text")
            )
            self._update_action_states()
            return
        try:
            updated = mod_mgr.set_enabled(
                mod, desired, lambda text: self._append_log("info", text)
            )
        except mod_mgr.ModError as exc:
            self._append_log("error", str(exc))
            self._set_mod_check(row, mod.enabled)
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))
            return
        self.mods[row] = updated
        self.config.active.set_enabled(updated.mod_id, desired)
        self._save_config()
        self._updating_mods = True
        try:
            self._style_mod_row(row, desired)
        finally:
            self._updating_mods = False
        self._refresh_profiles()
        self._refresh_mods_hint()

    def _add_mods(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        start = str(self.game.root / "Mods") if self.game else str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self, self.t("dialog.add_mods"), start, self.t("dialog.mod_filter")
        )
        if files:
            self._import_files(files)

    def _import_files(self, files: list[str]) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        paths = [Path(item) for item in files]
        unsupported = [
            path.name
            for path in paths
            if path.suffix.casefold() not in (".dll", ".zip")
        ]
        if unsupported:
            QMessageBox.warning(
                self, self.t("dialog.warning"), self.t("dialog.dropped_unsupported")
            )
        paths = [path for path in paths if path.suffix.casefold() in (".dll", ".zip")]
        if not paths:
            return
        root = self.game.root
        self._append_log("info", self.t("log.import_start", count=len(paths)))

        def job(log):
            return mod_mgr.import_paths(
                paths, root, log, origin="manual import"
            )

        self._run_task(job, self._on_import_done)

    def _refresh_game_state(self, *_) -> None:
        """Re-read backend/loader/version information from disk."""
        if self.game is not None:
            try:
                self.game = game.inspect_game(
                    self.game.root, self._steam_app_for(self.game.root)
                )
                self._game_running = game.is_game_running()
                if self._game_running and not self._play_timer.isActive():
                    self._launch_deadline = 0.0
                    self._play_timer.start()
            except Exception as exc:  # noqa: BLE001
                self._append_log("error", f"Could not refresh game state: {exc}")
        self._refresh_all()

    def _on_import_done(self, report: ImportReport) -> None:
        self._append_log(
            "success", self.t("log.import_done", count=len(report.installed))
        )
        for warning in report.warnings:
            self._append_log("warn", warning)
        if report.warnings:
            QMessageBox.warning(
                self, self.t("dialog.warning"), "\n".join(report.warnings[:12])
            )
        self._refresh_all()

    def _remove_selected_mod(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        mod = self._selected_mod()
        if mod is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("dialog.confirm"),
            self.t("mods.delete_confirm", name=mod.name, rel=mod.rel),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        root = self.game.root

        def job(log):
            mod_mgr.remove_mod(mod, root, log)
            return True

        self._run_task(job, lambda _result: self._refresh_all())

    def _refresh_loader_toggle_text(self) -> None:
        if self.game is not None and mod_mgr.loader_disabled(self.game.root):
            self.loader_toggle_btn.setText(self.t("loader.enable"))
        else:
            self.loader_toggle_btn.setText(self.t("loader.disable"))

    def _toggle_loader(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        root = self.game.root
        enable = mod_mgr.loader_disabled(root)

        def job(log):
            mod_mgr.set_loader_enabled(root, enable, log)
            return True

        self._run_task(job, lambda _result: self._refresh_all())

    def _check_mod_updates(self) -> None:
        if not self._require_game():
            return
        assert self.game is not None
        installed = []
        for mod in self.mods:
            if not mod.origin:
                continue
            split = thunderstore.split_download_origin(mod.origin)
            if split is None or not mod.version:
                continue
            installed.append((mod.mod_id, split[0], split[1], mod.version))
        if not installed:
            self._append_log("info", self.t("mods.no_updatable"))
            return
        client = self.client

        def job(log):
            return client.check_updates(installed, log)

        self._run_task(job, self._on_updates_checked)

    def _on_updates_checked(self, updates: dict) -> None:
        if not updates:
            self._append_log("success", self.t("mods.up_to_date"))
            return
        for mod_id, latest in updates.items():
            self._append_log(
                "info", self.t("mods.update_available", mod=mod_id, version=latest)
            )
        QMessageBox.information(
            self,
            self.t("mods.check_updates"),
            self.t("mods.updates_count", count=len(updates)),
        )

    def _open_mods_folder(self) -> None:
        if self.game is None:
            return
        self._open_path(mod_mgr.open_mods_folder(self.game.root))

    def _open_game_folder(self) -> None:
        if self.game is not None:
            self._open_path(self.game.root)

    def _open_path(self, path: Path) -> None:
        try:
            open_in_file_manager(path)
        except OSError as exc:
            self._append_log("error", f"Could not open {path}: {exc}")
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))

    # ------------------------------------------------------------------
    # Loader
    # ------------------------------------------------------------------

    def _on_loader_menu(self, action) -> None:
        if action is self.loader_download_action:
            self._install_loader_latest()
        elif action is self.loader_local_action:
            self._install_loader_from_file()

    def _install_loader_latest(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        root = self.game.root
        note = self.t("dialog.repair_note") if self.game.loader.installed else ""
        answer = QMessageBox.question(
            self,
            self.t("dialog.install_loader_title"),
            self.t("dialog.install_loader_text", version_note=note, path=root),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._append_log("info", self.t("log.loader_install_start", path=root))

        def job(log):
            return loader.install_latest(root, log)

        self._run_task(
            job,
            self._on_loader_installed,
            on_error=lambda message: self._offer_local_loader(message),
        )

    def _install_loader_from_file(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        root = self.game.root
        filename, _ = QFileDialog.getOpenFileName(
            self,
            self.t("dialog.install_loader_local"),
            str(Path.home()),
            self.t("dialog.zip_filter"),
        )
        if not filename:
            return
        archive_path = Path(filename)

        def job(log):
            return loader.install_from_zip(archive_path, root, log, source=str(archive_path))

        self._run_task(job, self._on_loader_installed)

    def _offer_local_loader(self, message: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.t("dialog.download_failed_title"))
        box.setText(self.t("dialog.download_failed_text", error=message))
        local_button = box.addButton(
            self.t("dialog.use_local"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(self.t("button.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is local_button:
            QTimer.singleShot(50, self._install_loader_from_file)
        return True

    def _on_loader_installed(self, report: ImportReport) -> None:
        self._append_log("success", self.t("log.loader_installed"))
        for warning in report.warnings:
            self._append_log("warn", warning)
        if self.game is not None:
            self.game = game.inspect_game(
                self.game.root, self._steam_app_for(self.game.root)
            )
        self._refresh_all()
        if report.warnings:
            QMessageBox.warning(
                self, self.t("dialog.warning"), "\n".join(report.warnings[:8])
            )

    def _uninstall_loader(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        if not self.game.loader.installed:
            return
        dialog = UninstallLoaderDialog(self, self.tr, str(self.game.root))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        root = self.game.root
        remove_mods = dialog.wants_mod_removal
        self._append_log("info", self.t("log.loader_uninstall_start"))

        def job(log):
            return loader.uninstall(root, log, remove_mods=remove_mods)

        self._run_task(job, self._on_loader_uninstalled)

    def _on_loader_uninstalled(self, report: ImportReport) -> None:
        self._append_log("success", self.t("log.loader_uninstalled"))
        for warning in report.warnings:
            self._append_log("warn", warning)
        if self.game is not None:
            self.game = game.inspect_game(
                self.game.root, self._steam_app_for(self.game.root)
            )
        self._refresh_all()
        if report.warnings:
            QMessageBox.warning(
                self, self.t("dialog.warning"), "\n".join(report.warnings[:8])
            )

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def _play(self) -> None:
        if self._game_running and self.game is not None and self.game.valid:
            self._stop_game()
            return
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        if not self.game.loader.installed:
            answer = QMessageBox.question(
                self,
                self.t("dialog.loader_missing_title"),
                self.t("dialog.loader_missing_text"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        changed, problems = mod_mgr.apply_profile(
            self.game.root,
            self.config.active,
            lambda text: self._append_log("info", text),
        )
        if problems:
            self._append_log("warn", self.t("log.profile_problems", count=len(problems)))
            QMessageBox.warning(self, self.t("dialog.warning"), "\n".join(problems[:8]))
        if changed:
            self._refresh_mods()
        try:
            launch_game(
                self.game,
                self.config.launch_method,
                self.config.extra_args,
                lambda text: self._append_log("info", text),
            )
        except LaunchError as exc:
            self._append_log("error", f"{self.t('log.launch_failed')} {exc}")
            QMessageBox.critical(self, self.t("dialog.error"), str(exc))
            return
        self._append_log(
            "info", self.t("log.launching", method=self.config.launch_method)
        )
        self._launch_deadline = time.monotonic() + 180
        self._game_running = False
        self._play_timer.start()
        self._update_action_states()

    def _stop_game(self) -> None:
        self._append_log("info", self.t("log.closing_game"))
        if not game.stop_game():
            self._append_log("warn", self.t("log.close_game_failed"))
        self._play_timer.start()
        self._update_action_states()

    def _poll_game_state(self) -> None:
        if self.game is None:
            self._play_timer.stop()
            return
        running = game.is_game_running()
        if running and not self._game_running:
            self._game_running = True
            self._append_log("success", self.t("log.game_started"))
            self._refresh_header()
            self._update_action_states()
        elif self._game_running and not running:
            self._game_running = False
            self._append_log("info", self.t("log.game_exited"))
            self._play_timer.stop()
            self._refresh_header()
            self._update_action_states()
            self._refresh_mods()
        elif not running and time.monotonic() > self._launch_deadline:
            self._play_timer.stop()
            self._append_log("warn", self.t("log.game_not_detected"))
            self._refresh_header()
            self._update_action_states()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def _on_profile_combo(self, name: str) -> None:
        if self._updating_profiles or not name:
            return
        if name == self.config.active_profile:
            return
        if self.game is None:
            self.config.active_profile = name
            self._save_config()
            return
        self._switch_profile(name)

    def _switch_profile(self, name: str) -> None:
        if not self._require_game() or not self._require_not_running():
            self._refresh_profiles()
            return
        if name not in self.config.profiles:
            return
        assert self.game is not None
        self.config.active_profile = name
        self._save_config()
        changed, problems = mod_mgr.apply_profile(
            self.game.root,
            self.config.active,
            lambda text: self._append_log("info", text),
        )
        self._append_log(
            "info", self.t("log.profile_switched", name=name, changed=changed)
        )
        if problems:
            self._append_log("warn", self.t("log.profile_problems", count=len(problems)))
            QMessageBox.warning(self, self.t("dialog.warning"), "\n".join(problems[:8]))
        self._refresh_all()

    def _export_profile(self) -> None:
        name = self._selected_profile() or self.config.active_profile
        profile = self.config.profiles.get(name)
        if profile is None:
            return
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            self.t("profiles.export_title"),
            f"{name}.json",
            "JSON (*.json)",
        )
        if not path_text:
            return
        try:
            mod_mgr.export_profile(profile, Path(path_text))
        except mod_mgr.ModError as exc:
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))
            return
        self._append_log("success", self.t("profiles.exported", name=name))

    def _import_profile(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self, self.t("profiles.import_title"), "", "JSON (*.json)"
        )
        if not path_text:
            return
        try:
            imported = mod_mgr.read_profile_file(Path(path_text))
        except mod_mgr.ModError as exc:
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))
            return
        name = imported.name
        if not self._validate_profile_name(name):
            return
        if name in self.config.profiles:
            name = self._unique_copy_name(name)
        imported.name = name
        self.config.profiles[name] = imported
        self._save_config()
        self._refresh_all()
        self._append_log("success", self.t("profiles.imported", name=name))

    def _selected_profile(self) -> str | None:
        row = self.profiles_table.currentRow()
        if row < 0:
            return None
        item = self.profiles_table.item(row, 0)
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return str(name) if name else None

    def _validate_profile_name(self, name: str) -> bool:
        if not name or any(char in name for char in '\\/:*?"<>|'):
            QMessageBox.warning(
                self, self.t("dialog.warning"), self.t("profiles.invalid")
            )
            return False
        return True

    def _new_profile(self) -> None:
        name, accepted = QInputDialog.getText(
            self, self.t("profiles.new_title"), self.t("profiles.name_label")
        )
        name = name.strip()
        if not accepted or not name:
            return
        if not self._validate_profile_name(name):
            return
        if name in self.config.profiles:
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("profiles.exists"))
            return
        self.config.profiles[name] = Profile(name=name)
        self._save_config()
        self._refresh_all()

    def _unique_copy_name(self, base: str) -> str:
        candidate = f"{base} {self.t('profiles.duplicate_suffix')}"
        if candidate not in self.config.profiles:
            return candidate
        counter = 2
        while f"{candidate} {counter}" in self.config.profiles:
            counter += 1
        return f"{candidate} {counter}"

    def _duplicate_profile(self) -> None:
        source_name = self._selected_profile() or self.config.active_profile
        source = self.config.profiles.get(source_name)
        if source is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            self.t("profiles.duplicate_title"),
            self.t("profiles.name_label"),
            text=self._unique_copy_name(source_name),
        )
        name = name.strip()
        if not accepted or not name:
            return
        if not self._validate_profile_name(name):
            return
        if name in self.config.profiles:
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("profiles.exists"))
            return
        self.config.profiles[name] = Profile(name=name, disabled=set(source.disabled))
        self._save_config()
        self._refresh_all()

    def _rename_profile(self) -> None:
        old_name = self._selected_profile()
        if old_name is None or old_name not in self.config.profiles:
            return
        new_name, accepted = QInputDialog.getText(
            self,
            self.t("profiles.rename_title"),
            self.t("profiles.name_label"),
            text=old_name,
        )
        new_name = new_name.strip()
        if not accepted or not new_name or new_name == old_name:
            return
        if not self._validate_profile_name(new_name):
            return
        if new_name in self.config.profiles:
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("profiles.exists"))
            return
        profile = self.config.profiles.pop(old_name)
        profile.name = new_name
        self.config.profiles[new_name] = profile
        if self.config.active_profile == old_name:
            self.config.active_profile = new_name
        self._save_config()
        self._refresh_all()

    def _delete_profile(self) -> None:
        if not self._require_not_running():
            return
        name = self._selected_profile()
        if name is None or name not in self.config.profiles:
            return
        if len(self.config.profiles) <= 1:
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("profiles.delete_last"))
            return
        answer = QMessageBox.question(
            self,
            self.t("dialog.confirm"),
            self.t("profiles.delete_confirm", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.profiles.pop(name, None)
        if self.config.active_profile == name:
            self.config.active_profile = next(iter(self.config.profiles))
            if self.game is not None:
                mod_mgr.apply_profile(self.game.root, self.config.active)
        self._save_config()
        self._refresh_all()

    def _activate_selected_profile(self) -> None:
        name = self._selected_profile()
        if name:
            self._switch_profile(name)

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Saves
    # ------------------------------------------------------------------

    def _refresh_saves(self) -> None:
        try:
            self.save_slots = saves.list_slots(self.config.save_labels)
        except Exception as exc:  # noqa: BLE001
            self.save_slots = []
            self._append_log("error", f"Could not list saves: {exc}")
        self.saves_table.setRowCount(len(self.save_slots))
        active_slot = self.config.active.save_slot
        for row, slot in enumerate(self.save_slots):
            name_item = QTableWidgetItem(slot.label)
            name_item.setData(Qt.ItemDataRole.UserRole, slot.key)
            self.saves_table.setItem(row, 0, name_item)
            self.saves_table.setItem(row, 1, QTableWidgetItem(slot.modified))
            self.saves_table.setItem(row, 2, QTableWidgetItem(human_size(slot.size_bytes)))
            linked = self._profiles_linked_to(slot.key)
            self.saves_table.setItem(row, 3, QTableWidgetItem(linked))
        self.saves_explain.setText(self.t("saves.explain"))
        self._update_action_states()

    def _profiles_linked_to(self, slot_key: str) -> str:
        names = [
            name for name, profile in self.config.profiles.items()
            if profile.save_slot == slot_key
        ]
        return ", ".join(names) if names else "—"

    def _selected_save(self) -> saves.SaveSlot | None:
        row = self.saves_table.currentRow()
        if 0 <= row < len(self.save_slots):
            return self.save_slots[row]
        return None

    def _rename_save(self) -> None:
        slot = self._selected_save()
        if slot is None:
            return
        new_name, accepted = QInputDialog.getText(
            self,
            self.t("saves.rename_title"),
            self.t("saves.name_label"),
            text=slot.label,
        )
        new_name = new_name.strip()
        if not accepted or not new_name:
            return
        self.config.save_labels[slot.key] = new_name
        self._save_config()
        self._refresh_saves()

    def _link_save_to_active(self) -> None:
        slot = self._selected_save()
        if slot is None:
            return
        saves.link_slot_to_profile(self.config.active, slot.key)
        self._save_config()
        self._refresh_saves()

    def _unlink_save(self) -> None:
        if self.config.active.save_slot is None:
            return
        saves.link_slot_to_profile(self.config.active, None)
        self._save_config()
        self._refresh_saves()

    def _delete_save(self) -> None:
        if not self._require_not_running():
            return
        slot = self._selected_save()
        if slot is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("dialog.confirm"),
            self.t("saves.delete_confirm", name=slot.label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            info = saves.delete_slot(slot, lambda text: self._append_log("info", text))
        except (saves.SaveError, backup.BackupError, OSError) as exc:
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))
            return
        for profile in self.config.profiles.values():
            if profile.save_slot == slot.key:
                profile.save_slot = None
        self.config.save_labels.pop(slot.key, None)
        self._save_config()
        if info is not None:
            self._append_log("success", self.t("log.backup_created", name=info.path.name))
        self._refresh_all()

    def _selected_backup(self) -> BackupInfo | None:
        row = self.backups_table.currentRow()
        if 0 <= row < len(self.backups):
            return self.backups[row]
        return None

    def _create_backup_now(self) -> None:
        if not self._require_game() or not self._require_not_running():
            return
        assert self.game is not None
        root = self.game.root
        paths = [
            root / "Mods",
            root / "Plugins",
            root / "UserLibs",
            root / "UserData",
            root / "MelonLoader",
        ]

        def job(log):
            return backup.create_backup("Manual backup", "manual", root, paths, log)

        self._run_task(job, self._on_backup_created)

    def _on_backup_created(self, info: BackupInfo | None) -> None:
        if info is None:
            self._append_log("info", self.t("backups.nothing"))
            return
        self._append_log("success", self.t("log.backup_created", name=info.path.name))
        self._refresh_backups()

    def _backup_saves(self) -> None:
        if self.game is None or self.game.save_dir is None:
            return
        saves = self.game.save_dir
        if not saves.is_dir():
            QMessageBox.information(
                self, self.t("dialog.warning"), self.t("header.saves_missing")
            )
            return

        def job(log):
            return backup.create_backup("Saves", "saves", saves, [saves], log)

        self._run_task(job, self._on_backup_created)

    def _restore_selected_backup(self) -> None:
        info = self._selected_backup()
        if info is None:
            return
        if not self._require_not_running():
            return
        if info.kind == "saves":
            base = Path(info.base_dir)
        else:
            if not self._require_game():
                return
            assert self.game is not None
            base = self.game.root
        answer = QMessageBox.question(
            self,
            self.t("dialog.confirm"),
            self.t("backups.restore_confirm", label=info.label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def job(log):
            return backup.restore_backup(info.id, base, log)

        def done(paths):
            self._append_log(
                "success", self.t("backups.restored") + f" ({len(paths)} files)"
            )

        self._run_task(job, done)

    def _delete_selected_backup(self) -> None:
        info = self._selected_backup()
        if info is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("dialog.confirm"),
            self.t("backups.delete_confirm", label=info.label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup.delete_backup(info.id)
        except backup.BackupError as exc:
            self._append_log("error", str(exc))
            QMessageBox.warning(self, self.t("dialog.error"), str(exc))
        self._refresh_backups()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_launch_method_changed(self, *_) -> None:
        if self._updating_settings:
            return
        method = self.launch_combo.currentData() or "steam"
        self.config.launch_method = str(method)
        self._save_config()
        if method == "steam" and self.config.extra_args.strip():
            self._append_log(
                "warn",
                "Extra launch arguments are ignored when launching through Steam.",
            )

    def _on_args_changed(self, *_) -> None:
        if self._updating_settings:
            return
        self.config.extra_args = self.args_edit.text().strip()
        self._save_config()

    def _on_language_changed(self, *_) -> None:
        if self._updating_settings:
            return
        language = self.language_combo.currentData() or "en"
        if language == self.tr.language:
            return
        self.tr.set_language(str(language))
        self.config.language = str(language)
        self._save_config()
        self._retranslate()

    def _on_theme_changed(self, *_) -> None:
        if self._updating_settings:
            return
        theme = self.theme_combo.currentData() or "dark"
        self.config.theme = str(theme)
        self._save_config()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, str(theme))
        # Custom row colours follow the palette, so rebuild the table.
        self._refresh_mods()

    # ------------------------------------------------------------------
    # Thunderstore
    # ------------------------------------------------------------------

    def _selected_ts_package(self) -> ThunderstorePackage | None:
        row = self.ts_table.currentRow()
        if 0 <= row < len(self.ts_results):
            return self.ts_results[row]
        return None

    def _search_thunderstore(self, force: bool = False) -> None:
        if self._tasks:
            self._append_log("warn", self.t("log.busy"))
            return
        query = self.ts_search.text()
        self._append_log("info", self.t("ts.searching"))
        client = self.client

        def job(log):
            if force:
                client.fetch_packages(force=True, log=log)
            return client.search(query, log=log)

        self._run_task(job, self._on_ts_results)

    def _refresh_thunderstore_index(self) -> None:
        self._search_thunderstore(force=True)

    def _on_ts_results(self, packages: list[ThunderstorePackage]) -> None:
        self.ts_results = packages
        self.ts_table.setRowCount(len(packages))
        for row, package in enumerate(packages):
            name_item = QTableWidgetItem(package.full_name)
            name_item.setToolTip(package.description)
            self.ts_table.setItem(row, 0, name_item)
            self.ts_table.setItem(row, 1, QTableWidgetItem(package.namespace))
            self.ts_table.setItem(row, 2, QTableWidgetItem(package.latest.version_number))
            self.ts_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{package.total_downloads:,}" if package.total_downloads else "—"
                ),
            )
            description = package.latest.description or package.description
            if len(description) > 130:
                description = description[:127] + "…"
            self.ts_table.setItem(row, 4, QTableWidgetItem(description))
        if packages:
            self.ts_status.setText(self.t("ts.loaded", count=len(packages)))
        else:
            self.ts_status.setText(self.t("ts.no_results"))
        self._update_action_states()

    def _install_thunderstore(self) -> None:
        package = self._selected_ts_package()
        if package is None:
            QMessageBox.information(
                self, self.t("dialog.warning"), self.t("ts.select_first")
            )
            return
        if not self._require_game() or not self._require_not_running():
            return
        if self.game is not None and not self.game.loader.installed:
            self._append_log("warn", self.t("ts.requires_loader"))
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("ts.requires_loader"))
        self._pending_package = package
        client = self.client
        self._append_log("info", f"Resolving dependencies for {package.full_name}…")

        def job(log):
            return client.resolve(package, log=log)

        self._run_task(job, self._on_ts_resolved)

    def _on_ts_resolved(self, result) -> None:
        package = self._pending_package
        if package is None:
            return
        ordered, missing, notes = result
        dialog = DependencyDialog(
            self, self.tr, package.full_name, ordered, missing, notes
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        assert self.game is not None
        root = self.game.root
        client = self.client
        self._append_log(
            "info", self.t("ts.installing", count=len(ordered))
        )
        # Let the previous QThread finish before starting the next task.
        QTimer.singleShot(60, lambda: self._start_ts_install(ordered, root, client))

    def _start_ts_install(self, ordered: list[ResolvedPackage], root: Path, client) -> None:
        def job(log):
            report = ImportReport()
            for resolved in ordered:
                archive_path = client.download(
                    resolved.package, resolved.version, log
                )
                partial = mod_mgr.import_archive(
                    archive_path,
                    root,
                    log,
                    author=resolved.package.namespace,
                    origin=resolved.version.download_url,
                )
                report.installed.extend(partial.installed)
                report.skipped.extend(partial.skipped)
                report.warnings.extend(partial.warnings)
            return report

        self._run_task(job, self._on_ts_installed)

    def _on_ts_installed(self, report: ImportReport) -> None:
        self._append_log(
            "success", self.t("ts.installed", count=len(report.installed))
        )
        for warning in report.warnings:
            self._append_log("warn", warning)
        if report.warnings:
            QMessageBox.warning(
                self, self.t("dialog.warning"), "\n".join(report.warnings[:10])
            )
        self._refresh_all()

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._tasks:
            QMessageBox.information(
                self, self.t("dialog.warning"), self.t("log.busy")
            )
            event.ignore()
            return
        self._play_timer.stop()
        self._save_config()
        super().closeEvent(event)
