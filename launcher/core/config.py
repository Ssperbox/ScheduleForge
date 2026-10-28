"""Application paths, configuration persistence and lightweight logging."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .models import APP_SLUG, AppConfig, Profile

LOGGER = logging.getLogger(__name__)


def app_data_dir() -> Path:
    """Per-user writable directory: ``%APPDATA%\\ScheduleIModLauncher``.

    On non-Windows systems an XDG-style fallback is used so the app can be
    developed and tested outside Windows.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / APP_SLUG


def config_path() -> Path:
    return app_data_dir() / "config.json"


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def cache_dir() -> Path:
    return app_data_dir() / "cache"


def backups_dir() -> Path:
    return app_data_dir() / "backups"


def ensure_app_dirs() -> None:
    for directory in (app_data_dir(), logs_dir(), cache_dir(), backups_dir()):
        directory.mkdir(parents=True, exist_ok=True)


def _load_labels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(label)
        for key, label in value.items()
        if isinstance(key, str) and isinstance(label, str) and label.strip()
    }


def _profile_payload(profile: Profile) -> dict[str, object]:
    payload: dict[str, object] = {"disabled": sorted(profile.disabled)}
    if profile.save_slot:
        payload["save_slot"] = profile.save_slot
    return payload


class ConfigStore:
    """Loads/saves :class:`AppConfig` atomically as JSON.

    The config lives in ``%APPDATA%`` (not next to the executable) so that it
    survives app updates and works when the app is installed under Program
    Files. Saves go through a temporary file + ``os.replace`` to avoid leaving a
    truncated config behind if the process is killed.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self.load_warnings: list[str] = []

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            return self._directory
        return app_data_dir()

    @property
    def path(self) -> Path:
        return self.directory / "config.json"

    def load(self) -> AppConfig:
        self.load_warnings = []
        if not self.path.is_file():
            return AppConfig(profiles={"Default": Profile("Default")})
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.load_warnings.append(f"Could not read config ({exc}); defaults used.")
            self._quarantine_corrupt_file()
            return AppConfig(profiles={"Default": Profile("Default")})

        if not isinstance(raw, dict):
            self.load_warnings.append("Config has an unexpected shape; defaults used.")
            return AppConfig(profiles={"Default": Profile("Default")})

        profiles: dict[str, Profile] = {}
        raw_profiles = raw.get("profiles")
        if isinstance(raw_profiles, dict):
            for name, value in raw_profiles.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                disabled: set[str] = set()
                save_slot: str | None = None
                if isinstance(value, dict):
                    entries = value.get("disabled", [])
                    if isinstance(entries, list):
                        disabled = {str(item) for item in entries if isinstance(item, str)}
                    slot = value.get("save_slot")
                    if isinstance(slot, str) and slot:
                        save_slot = slot
                profiles[name] = Profile(name=name, disabled=disabled, save_slot=save_slot)

        config = AppConfig(
            game_path=raw.get("game_path") if isinstance(raw.get("game_path"), str) else None,
            launch_method=str(raw.get("launch_method", "steam")),
            language=str(raw.get("language", "en")),
            theme=str(raw.get("theme", "dark")),
            active_profile=str(raw.get("active_profile", "Default")),
            profiles=profiles,
            extra_args=str(raw.get("extra_args", "")),
            save_labels=_load_labels(raw.get("save_labels")),
        )
        config.ensure_defaults()
        return config

    def save(self, config: AppConfig) -> None:
        """Persist the config; raises :class:`OSError` on failure."""
        config.ensure_defaults()
        payload = {
            "schema": 1,
            "game_path": config.game_path,
            "launch_method": config.launch_method,
            "language": config.language,
            "theme": config.theme,
            "active_profile": config.active_profile,
            "extra_args": config.extra_args,
            "profiles": {
                name: _profile_payload(profile)
                for name, profile in config.profiles.items()
            },
            "save_labels": dict(sorted(config.save_labels.items())),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="config-", suffix=".tmp", dir=str(self.directory)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _quarantine_corrupt_file(self) -> None:
        try:
            broken = self.path.with_suffix(".json.corrupt")
            os.replace(self.path, broken)
            self.load_warnings.append(f"Broken config moved to {broken.name}.")
        except OSError:
            LOGGER.warning("Could not quarantine broken config", exc_info=True)


def setup_file_logging() -> Path:
    """Configure a rotating-ish daily log file and return its path."""
    ensure_app_dirs()
    path = logs_dir() / "launcher.log"
    if path.exists() and path.stat().st_size > 2_000_000:
        try:
            path.replace(logs_dir() / "launcher.old.log")
        except OSError:
            pass
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers when the UI is re-created (e.g. tests).
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    return path
