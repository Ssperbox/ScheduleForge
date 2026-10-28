"""Core tests. Run with: py -m unittest discover -s tests -v

All tests redirect %APPDATA% to a temporary folder so they never touch the real
user configuration, backups or game installation.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from launcher.core import (
    archive,
    backup,
    config,
    game,
    launch,
    loader,
    mod_mgr,
    platform_win,
    saves,
    steam_detect,
    vdf,
)
from launcher.core.models import (
    DISABLED_SUFFIX,
    AppConfig,
    Backend,
    GameInstall,
    Profile,
)
from launcher.core.thunderstore import (
    ThunderstoreClient,
    _parse_package,
    split_dependency,
    split_download_origin,
)


def make_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive_file:
        for name, data in files.items():
            archive_file.writestr(name, data)
    return path


class TempAppData(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self._env = mock.patch.dict(
            os.environ,
            {
                "APPDATA": str(self.root / "appdata"),
                "XDG_CONFIG_HOME": str(self.root / "appdata"),
            },
        )
        self._env.start()
        (self.root / "appdata").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._env.stop()
        self._temp.cleanup()


class ProcessDetectionTests(unittest.TestCase):
    def test_running_process_is_detected(self) -> None:
        output = (
            '"Schedule I.exe","1234","Console","1","500,000 K","Running",'
            '"PC\\user","0:01:00","N/A"\n'
        )
        with mock.patch.object(platform_win, "is_windows", return_value=True), mock.patch(
            "subprocess.run"
        ) as run:
            run.return_value = mock.Mock(stdout=output)
            self.assertTrue(platform_win.is_process_running("Schedule I.exe"))
            command = run.call_args.args[0]
            self.assertTrue(any("STATUS" in part for part in command))
            self.assertTrue(any("Unknown" in part for part in command))

    def test_exited_zombie_is_not_running(self) -> None:
        output = (
            '"Schedule I.exe","38596","Console","1","36 K","Unknown",'
            '"PC\\user","0:00:00","N/A"\n'
        )
        with mock.patch.object(platform_win, "is_windows", return_value=True), mock.patch(
            "subprocess.run"
        ) as run:
            run.return_value = mock.Mock(stdout=output)
            self.assertFalse(platform_win.is_process_running("Schedule I.exe"))

    def test_no_tasks_message_is_not_running(self) -> None:
        with mock.patch.object(platform_win, "is_windows", return_value=True), mock.patch(
            "subprocess.run"
        ) as run:
            run.return_value = mock.Mock(
                stdout="INFO: No tasks are running which match the specified criteria.\n"
            )
            self.assertFalse(platform_win.is_process_running("Schedule I.exe"))


class LaunchTests(TempAppData):
    def test_direct_launch_starts_background_reaper(self) -> None:
        root = self.root / "game"
        root.mkdir()
        exe = root / "Schedule I.exe"
        exe.write_bytes(b"MZ")
        install = GameInstall(root=root, exe=exe)
        fake_process = mock.Mock(pid=4321)

        with mock.patch.object(
            launch.subprocess, "Popen", return_value=fake_process
        ), mock.patch.object(launch.threading, "Thread") as thread:
            result = launch.launch_game(install, "direct")

        self.assertEqual(result.pid, 4321)
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        self.assertEqual(thread.call_args.kwargs["args"][1], 4321)
        thread.return_value.start.assert_called_once()


class VdfTests(unittest.TestCase):
    def test_modern_and_legacy_library_formats(self) -> None:
        tree = vdf.parse(
            '''
            "libraryfolders"
            {
                "contentstatsid" "12345"
                "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" "label" "" }
                "1" { "path" "D:\\\\SteamLibrary" }
                "2" "E:\\\\OldLibrary"
            }
            '''
        )
        paths = vdf.iter_library_paths(tree)
        self.assertIn("C:\\Program Files (x86)\\Steam", paths)
        self.assertIn("D:\\SteamLibrary", paths)
        self.assertIn("E:\\OldLibrary", paths)

    def test_escaped_quotes_and_comments(self) -> None:
        tree = vdf.parse(
            '// comment\n"AppState" { "installdir" "Schedule I" "note" "a \\"quote\\"" }'
        )
        self.assertEqual(tree["AppState"]["installdir"], "Schedule I")
        self.assertEqual(tree["AppState"]["note"], 'a "quote"')

    def test_case_insensitive_lookup(self) -> None:
        self.assertEqual(vdf.get_ci({"Path": "x"}, "path"), "x")


class GameDetectionTests(TempAppData):
    def make_game(self) -> Path:
        root = self.root / "Schedule I"
        (root / "Schedule I_Data" / "Managed").mkdir(parents=True)
        (root / "Schedule I.exe").write_bytes(b"MZ")
        (root / "GameAssembly.dll").write_bytes(b"il2cpp")
        return root

    def test_validation_and_backend(self) -> None:
        root = self.make_game()
        self.assertTrue(game.validate_game_dir(root).ok)
        self.assertIs(game.detect_backend(root), Backend.IL2CPP)
        install = game.inspect_game(root)
        self.assertTrue(install.valid)
        self.assertTrue(any("IL2CPP" in warning for warning in install.warnings))

    def test_mono_detection(self) -> None:
        root = self.root / "MonoGame"
        (root / "Schedule I_Data" / "Managed").mkdir(parents=True)
        (root / "Schedule I.exe").write_bytes(b"MZ")
        (root / "Schedule I_Data" / "Managed" / "Assembly-CSharp.dll").write_bytes(b"mono")
        self.assertIs(game.detect_backend(root), Backend.MONO)

    def test_rejects_wrong_folder(self) -> None:
        root = self.root / "NotTheGame"
        root.mkdir()
        result = game.validate_game_dir(root)
        self.assertFalse(result.ok)
        self.assertIn("Schedule I.exe", result.message)

    def test_appmanifest_parsing(self) -> None:
        library = self.root / "Library"
        (library / "steamapps" / "common" / "Schedule I").mkdir(parents=True)
        (library / "steamapps" / "appmanifest_3164500.acf").write_text(
            '"AppState" { "appid" "3164500" "installdir" "Schedule I" '
            '"buildid" "999" "LastUpdated" "1700000000" }',
            encoding="utf-8",
        )
        with mock.patch.object(steam_detect, "all_libraries", return_value=[library]):
            locations = steam_detect.find_app()
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].build_id, "999")
        self.assertEqual(locations[0].install_dir.name, "Schedule I")


class ArchiveTests(TempAppData):
    def test_rejects_zip_slip(self) -> None:
        evil = make_zip(self.root / "evil.zip", {"../escape.dll": b"x"})
        with self.assertRaises(archive.ArchiveError):
            archive.extract_zip(evil, self.root / "target")
        self.assertFalse((self.root / "escape.dll").exists())

    def test_rejects_absolute_paths(self) -> None:
        evil = make_zip(self.root / "absolute.zip", {"C:/Windows/evil.dll": b"x"})
        with self.assertRaises(archive.ArchiveError):
            archive.extract_zip(evil, self.root / "target")

    def test_rejects_alternate_data_streams(self) -> None:
        evil = make_zip(
            self.root / "ads.zip", {"Mods/legit.dll:payload": b"hidden"}
        )
        with self.assertRaises(archive.ArchiveError):
            archive.extract_zip(evil, self.root / "target")

    def test_extracts_safely(self) -> None:
        good = make_zip(self.root / "good.zip", {"Mods/a.dll": b"data", "readme.md": b"hi"})
        written = archive.extract_zip(good, self.root / "target")
        self.assertEqual(len(written), 2)
        self.assertTrue((self.root / "target" / "Mods" / "a.dll").is_file())


class ConfigTests(TempAppData):
    def test_roundtrip_and_profiles(self) -> None:
        store = config.ConfigStore(self.root / "cfg")
        data = store.load()
        data.game_path = "C:/Games/Schedule I"
        data.profiles["Testing"] = Profile("Testing", {"Mods/a.dll"})
        data.active_profile = "Testing"
        store.save(data)

        reloaded = config.ConfigStore(self.root / "cfg").load()
        self.assertEqual(reloaded.game_path, "C:/Games/Schedule I")
        self.assertIn("Testing", reloaded.profiles)
        self.assertEqual(reloaded.active_profile, "Testing")
        self.assertFalse(reloaded.active.is_enabled("Mods/a.dll"))
        self.assertTrue(reloaded.active.is_enabled("Mods/b.dll"))

    def test_corrupt_config_is_quarantined(self) -> None:
        store = config.ConfigStore(self.root / "cfg")
        store.path.parent.mkdir(parents=True)
        store.path.write_text("{not json", encoding="utf-8")
        loaded = store.load()
        self.assertIn("Default", loaded.profiles)
        self.assertTrue(store.path.with_suffix(".json.corrupt").exists())


class LoaderTests(TempAppData):
    def make_game(self) -> Path:
        root = self.root / "game"
        (root / "Schedule I_Data").mkdir(parents=True)
        (root / "Schedule I.exe").write_bytes(b"MZ")
        return root

    def make_loader_zip(self) -> Path:
        return make_zip(
            self.root / "MelonLoader.x64.zip",
            {
                "MelonLoader/MelonLoader.dll": b"core",
                "MelonLoader/net6/MelonLoader.dll": b"net6",
                "version.dll": b"proxy",
            },
        )

    def test_install_detect_uninstall(self) -> None:
        root = self.make_game()
        report = loader.install_from_zip(self.make_loader_zip(), root, version="0.7.3")
        self.assertTrue(report.installed)
        status = loader.detect(root)
        self.assertTrue(status.installed)
        self.assertEqual(status.version, "0.7.3")
        self.assertEqual(status.proxy_dll, "version.dll")

        loader.uninstall(root)
        self.assertFalse(loader.detect(root).installed)
        self.assertFalse((root / "version.dll").exists())
        self.assertTrue((root / "Schedule I.exe").exists())

    def test_install_backs_up_existing_loader(self) -> None:
        root = self.make_game()
        loader.install_from_zip(self.make_loader_zip(), root, version="0.7.3")
        loader.install_from_zip(self.make_loader_zip(), root, version="0.7.3")
        backups = backup.list_backups()
        self.assertTrue(any(item.kind == "loader-install" for item in backups))

    def test_rejects_non_loader_zip(self) -> None:
        root = self.make_game()
        bad = make_zip(self.root / "bad.zip", {"Mods/a.dll": b"x"})
        with self.assertRaises(loader.LoaderError):
            loader.install_from_zip(bad, root)

    def test_uninstall_can_keep_mods(self) -> None:
        root = self.make_game()
        loader.install_from_zip(self.make_loader_zip(), root, version="0.7.3")
        (root / "Mods").mkdir()
        (root / "Mods" / "a.dll").write_bytes(b"mod")
        loader.uninstall(root, remove_mods=False)
        self.assertTrue((root / "Mods" / "a.dll").is_file())


class ModManagerTests(TempAppData):
    def make_game(self) -> Path:
        root = self.root / "game"
        (root / "Schedule I_Data").mkdir(parents=True)
        (root / "Schedule I.exe").write_bytes(b"MZ")
        (root / "Mods").mkdir()
        return root

    def test_scan_toggle_and_profile(self) -> None:
        root = self.make_game()
        (root / "Mods" / "Example.dll").write_bytes(b"mod")
        mod_mgr._write_sidecar(
            root / "Mods" / "Example.dll", name="Example Mod", version="1.0.0", author="Author"
        )
        mods = mod_mgr.scan(root)
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0].name, "Example Mod")
        self.assertTrue(mods[0].enabled)

        updated = mod_mgr.set_enabled(mods[0], False)
        self.assertTrue(updated.path.name.endswith(DISABLED_SUFFIX))
        self.assertFalse(mod_mgr.scan(root)[0].enabled)

        profile = Profile("Vanilla", {"Mods/Example.dll"})
        changed, problems = mod_mgr.apply_profile(root, profile)
        self.assertEqual(changed, 0)  # already disabled by the toggle
        self.assertFalse(problems)

        mod_mgr.set_enabled(mod_mgr.scan(root)[0], True)
        changed, _ = mod_mgr.apply_profile(root, profile)
        self.assertEqual(changed, 1)
        self.assertFalse(mod_mgr.scan(root)[0].enabled)

    def test_profile_export_import_roundtrip(self) -> None:
        profile = Profile("Shared", {"Mods/Example.dll"}, save_slot="slot1")
        path = self.root / "shared.json"
        mod_mgr.export_profile(profile, path)
        loaded = mod_mgr.read_profile_file(path)
        self.assertEqual(loaded.name, "Shared")
        self.assertEqual(loaded.disabled, {"Mods/Example.dll"})
        self.assertEqual(loaded.save_slot, "slot1")

    def test_profile_import_rejects_foreign_json(self) -> None:
        path = self.root / "other.json"
        path.write_text('{"name": "x"}', encoding="utf-8")
        with self.assertRaises(mod_mgr.ModError):
            mod_mgr.read_profile_file(path)

    def test_loader_disable_and_enable_renames_proxy(self) -> None:
        root = self.make_game()
        proxy = root / "version.dll"
        proxy.write_bytes(b"proxy")
        self.assertFalse(mod_mgr.loader_disabled(root))

        mod_mgr.set_loader_enabled(root, False)
        self.assertFalse(proxy.exists())
        self.assertTrue((root / ("version.dll" + DISABLED_SUFFIX)).is_file())
        self.assertTrue(mod_mgr.loader_disabled(root))

        mod_mgr.set_loader_enabled(root, True)
        self.assertTrue(proxy.is_file())
        self.assertFalse(mod_mgr.loader_disabled(root))

    def test_import_zip_routes_folders(self) -> None:
        root = self.make_game()
        package = make_zip(
            self.root / "pkg.zip",
            {
                "manifest.json": b'{"name":"TestMod","version_number":"2.0.0",'
                b'"dependencies":["Auth-Dep-1.0.0"],"description":"demo"}',
                "icon.png": b"png",
                "Mods/TestMod.dll": b"dll",
                "UserData/TestMod/config.json": b"{}",
            },
        )
        report = mod_mgr.import_archive(package, root, lambda _msg: None, author="Auth")
        self.assertIn("Mods/TestMod.dll", report.installed)
        self.assertTrue((root / "UserData" / "TestMod" / "config.json").is_file())
        mods = mod_mgr.scan(root)
        self.assertEqual(mods[0].name, "TestMod")
        self.assertEqual(mods[0].version, "2.0.0")
        self.assertEqual(mods[0].author, "Auth")

    def test_import_rejects_zip_slip(self) -> None:
        root = self.make_game()
        package = make_zip(self.root / "evil.zip", {"../evil.dll": b"x"})
        with self.assertRaises(mod_mgr.ModError):
            mod_mgr.import_archive(package, root, lambda _msg: None)
        self.assertFalse((self.root / "evil.dll").exists())

    def test_import_skips_bepinex_with_warning(self) -> None:
        root = self.make_game()
        package = make_zip(
            self.root / "bep.zip",
            {"BepInEx/plugins/Thing.dll": b"x", "Mods/Good.dll": b"y"},
        )
        report = mod_mgr.import_archive(package, root, lambda _msg: None)
        self.assertTrue((root / "Mods" / "Good.dll").is_file())
        self.assertTrue(any("BepInEx" in warning for warning in report.warnings))

    def test_manifest_name_cannot_escape_userdata(self) -> None:
        root = self.make_game()
        package = make_zip(
            self.root / "hostile-name.zip",
            {
                "manifest.json": b'{"name":"../../escape","version_number":"1.0.0"}',
                "settings.json": b"{}",
            },
        )
        mod_mgr.import_archive(package, root, lambda _msg: None)
        escaped = self.root / "escape" / "settings.json"
        self.assertFalse(escaped.exists())
        self.assertTrue(list((root / "UserData").rglob("settings.json")))

    def test_remove_mod_creates_backup(self) -> None:
        root = self.make_game()
        (root / "Mods" / "Doomed.dll").write_bytes(b"mod")
        mod = mod_mgr.scan(root)[0]
        mod_mgr.remove_mod(mod, root, lambda _msg: None)
        self.assertFalse((root / "Mods" / "Doomed.dll").exists())
        self.assertTrue(any(item.kind == "mod-delete" for item in backup.list_backups()))


class BackupTests(TempAppData):
    def test_backup_and_restore(self) -> None:
        base = self.root / "game"
        base.mkdir()
        target = base / "Mods" / "a.dll"
        target.parent.mkdir()
        target.write_bytes(b"original")
        info = backup.create_backup("test", "manual", base, [target.parent])
        self.assertIsNotNone(info)
        target.write_bytes(b"modified")

        restored = backup.restore_backup(info.id, base)
        self.assertEqual(restored, ["Mods/a.dll"])
        self.assertEqual(target.read_bytes(), b"original")
        # Restore itself took a safety backup.
        self.assertTrue(any(item.kind == "pre-restore" for item in backup.list_backups()))

    def test_backup_id_traversal_rejected(self) -> None:
        with self.assertRaises(backup.BackupError):
            backup.restore_backup("../config")


class SaveSlotTests(TempAppData):
    def _make_saves(self) -> Path:
        saves_root = self.root / "Saves"
        (saves_root / "user-1").mkdir(parents=True)
        (saves_root / "user-1" / "save.dat").write_bytes(b"progress")
        (saves_root / "user-2").mkdir()
        return saves_root

    def test_list_uses_launcher_labels(self) -> None:
        saves_root = self._make_saves()
        with mock.patch.object(game, "default_save_dir", return_value=saves_root):
            slots = saves.list_slots({"user-1": "Main run"})
        by_key = {slot.key: slot for slot in slots}
        self.assertEqual(by_key["user-1"].label, "Main run")
        self.assertEqual(by_key["user-2"].label, "user-2")
        self.assertEqual(by_key["user-1"].size_bytes, len(b"progress"))

    def test_delete_creates_backup_first(self) -> None:
        saves_root = self._make_saves()
        with mock.patch.object(game, "default_save_dir", return_value=saves_root):
            slot = next(s for s in saves.list_slots() if s.key == "user-1")
            info = saves.delete_slot(slot)
        self.assertIsNotNone(info)
        self.assertFalse((saves_root / "user-1").exists())
        self.assertTrue(info.path.is_file())

    def test_delete_refuses_unexpected_folder(self) -> None:
        stray = self.root / "elsewhere" / "user-9"
        stray.mkdir(parents=True)
        slot = saves.SaveSlot(folder=stray, label="x", modified="", size_bytes=0)
        with self.assertRaises(saves.SaveError):
            saves.delete_slot(slot)
        self.assertTrue(stray.is_dir())

    def test_link_and_labels_roundtrip(self) -> None:
        profile = Profile("Run")
        saves.link_slot_to_profile(profile, "user-1")
        self.assertEqual(profile.save_slot, "user-1")
        saves.link_slot_to_profile(profile, None)
        self.assertIsNone(profile.save_slot)

        store = config.ConfigStore(self.root / "cfg")
        data = AppConfig(profiles={"Run": Profile("Run", save_slot="user-1")})
        data.save_labels["user-1"] = "Main run"
        store.save(data)
        reloaded = config.ConfigStore(self.root / "cfg").load()
        self.assertEqual(reloaded.profiles["Run"].save_slot, "user-1")
        self.assertEqual(reloaded.save_labels, {"user-1": "Main run"})


class ThunderstoreTests(unittest.TestCase):
    def test_split_dependency(self) -> None:
        self.assertEqual(split_dependency("LavaGang-MelonLoader-0.7.3"), ("LavaGang", "MelonLoader", "0.7.3"))
        self.assertIsNone(split_dependency("nonsense"))

    def test_parse_package_and_resolve_order(self) -> None:
        raw = {
            "namespace": "Author",
            "name": "Base",
            "full_name": "Author-Base",
            "total_downloads": 100,
            "versions": [
                {
                    "version_number": "1.0.0",
                    "download_url": "https://thunderstore.io/package/download/Author/Base/1.0.0/",
                    "dependencies": ["Lib-Core-2.0.0"],
                    "file_size": 10,
                }
            ],
        }
        package = _parse_package(raw)
        self.assertIsNotNone(package)
        self.assertEqual(package.latest.version_number, "1.0.0")

        lib_package = _parse_package(
            {
                "namespace": "Lib",
                "name": "Core",
                "full_name": "Lib-Core",
                "versions": [
                    {
                        "version_number": "2.0.0",
                        "download_url": "https://thunderstore.io/package/download/Lib/Core/2.0.0/",
                        "dependencies": [],
                    }
                ],
            }
        )
        client = ThunderstoreClient()
        client._memory = [package, lib_package]
        ordered, missing, notes = client.resolve(package)
        self.assertEqual([item.package.name for item in ordered], ["Core", "Base"])
        self.assertFalse(missing)
        self.assertFalse(notes)

    def test_split_download_origin(self) -> None:
        self.assertEqual(
            split_download_origin("https://thunderstore.io/package/download/Author/Mod/1.2.0/"),
            ("Author", "Mod"),
        )
        self.assertIsNone(split_download_origin("https://example.com/file.zip"))
        self.assertIsNone(split_download_origin(None))

    def test_loader_dependency_is_a_note_not_a_mod(self) -> None:
        package = _parse_package(
            {
                "namespace": "A",
                "name": "Mod",
                "versions": [
                    {
                        "version_number": "1.0.0",
                        "download_url": "https://thunderstore.io/x.zip",
                        "dependencies": ["LavaGang-MelonLoader-0.7.3"],
                    }
                ],
            }
        )
        client = ThunderstoreClient()
        client._memory = [package]
        ordered, missing, notes = client.resolve(package)
        self.assertEqual([item.package.name for item in ordered], ["Mod"])
        self.assertFalse(missing)
        self.assertTrue(any("MelonLoader" in note for note in notes))


if __name__ == "__main__":
    unittest.main()
