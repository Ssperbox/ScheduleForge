"""Headless UI smoke test (uses Qt's 'offscreen' platform).

Set QT_QPA_PLATFORM=offscreen when running manually:
    py -m unittest tests.test_ui_smoke -v
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from launcher.core import mod_mgr  # noqa: E402
from launcher.core.config import ConfigStore  # noqa: E402
from launcher.core.models import Profile  # noqa: E402
from launcher.ui import main_window as main_window_module  # noqa: E402
from launcher.ui.main_window import MainWindow  # noqa: E402


def make_fake_game(root: Path) -> Path:
    game_root = root / "Schedule I"
    (game_root / "Schedule I_Data" / "Managed").mkdir(parents=True)
    (game_root / "Schedule I.exe").write_bytes(b"MZ")
    (game_root / "GameAssembly.dll").write_bytes(b"il2cpp")
    (game_root / "Mods").mkdir()
    (game_root / "Mods" / "ExampleMod.dll").write_bytes(b"dll")
    mod_mgr._write_sidecar(
        game_root / "Mods" / "ExampleMod.dll",
        name="Example Mod",
        version="1.2.3",
        author="Tester",
    )
    return game_root


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self._env = mock.patch.dict(
            os.environ,
            {
                "APPDATA": str(self.root / "appdata"),
                "XDG_CONFIG_HOME": str(self.root / "appdata"),
                "QT_QPA_PLATFORM": "offscreen",
            },
        )
        self._env.start()
        self.game_root = make_fake_game(self.root)
        self.store = ConfigStore(self.root / "config")
        config = self.store.load()
        config.game_path = str(self.game_root)
        config.profiles["Vanilla"] = Profile("Vanilla")
        self.store.save(config)
        # Never let a real game instance on the developer's PC turn a UI test
        # into a blocking "close the game" dialog.
        self._running = mock.patch.object(
            main_window_module.game, "is_game_running", return_value=False
        )
        self._running.start()

    def tearDown(self) -> None:
        self._running.stop()
        self._env.stop()
        self._temp.cleanup()

    def _pump(self, seconds: float = 0.6) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_window_loads_game_and_toggles_mod(self) -> None:
        window = MainWindow(store=ConfigStore(self.root / "config"))
        self._pump(0.8)
        self.assertIsNotNone(window.game)
        self.assertEqual(window.tabs.count(), 6)
        self.assertEqual(window.mods_table.rowCount(), 1)
        self.assertEqual(window.mods_table.item(0, 1).text(), "Example Mod")
        self.assertEqual(window.mods_table.item(0, 2).text(), "1.2.3")

        window.mods_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self._pump(0.2)
        disabled = self.game_root / "Mods" / "ExampleMod.dll.disabled"
        self.assertTrue(disabled.is_file())
        self.assertIn("Mods/ExampleMod.dll", window.config.active.disabled)
        self.assertEqual(window.mods_table.item(0, 5).text(), "Disabled")

        window.mods_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        self._pump(0.2)
        self.assertTrue((self.game_root / "Mods" / "ExampleMod.dll").is_file())

        window.language_combo.setCurrentIndex(window.language_combo.findData("es"))
        self._pump(0.2)
        self.assertEqual(window.tabs.tabText(5), "Ajustes")
        self.assertEqual(window.add_mod_btn.text(), "Añadir mod…")

        window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
        self._pump(0.2)
        window.close()
        self._pump(0.1)

    def test_settings_scrolls_instead_of_clipping(self) -> None:
        window = MainWindow(store=ConfigStore(self.root / "config"))
        window.resize(1100, 700)
        window.tabs.setCurrentIndex(5)
        window.show()
        window.centralWidget().layout().activate()
        self._pump(0.4)

        tab = window.tabs.widget(5)
        self.assertIsInstance(tab, QScrollArea)
        self.assertTrue(tab.widgetResizable())
        self.assertGreater(tab.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(window.launch_combo.height(), 32)
        self.assertGreaterEqual(window.language_combo.height(), 32)
        content = tab.widget()
        self.assertGreaterEqual(content.height(), content.minimumSizeHint().height())
        window.close()
        self._pump(0.1)

    def test_thunderstore_table_population(self) -> None:
        from launcher.core.thunderstore import _parse_package

        window = MainWindow(store=ConfigStore(self.root / "config"))
        self._pump(0.5)
        package = _parse_package(
            {
                "namespace": "Author",
                "name": "CoolMod",
                "versions": [
                    {
                        "version_number": "1.0.0",
                        "download_url": "https://thunderstore.io/package/download/Author/CoolMod/1.0.0/",
                        "dependencies": [],
                        "description": "A cool mod",
                    }
                ],
            }
        )
        window._on_ts_results([package])
        self.assertEqual(window.ts_table.rowCount(), 1)
        self.assertEqual(window.ts_table.item(0, 0).text(), "Author-CoolMod")
        window.close()
        self._pump(0.1)


if __name__ == "__main__":
    unittest.main()
