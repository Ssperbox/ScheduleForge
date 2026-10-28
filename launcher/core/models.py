"""Typed data structures shared by the launcher core and the UI.

The core layer never imports Qt: everything here is plain Python so it can be
unit-tested and reused from a CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Application constants
# ---------------------------------------------------------------------------

APP_NAME = "Schedule Forge"
APP_SLUG = "ScheduleIModLauncher"
APP_VERSION = "1.0.0"

GAME_NAME = "Schedule I"
STEAM_APP_ID = "3164500"
GAME_EXE_NAME = "Schedule I.exe"
GAME_DATA_DIR = "Schedule I_Data"
GAME_FOLDER_NAME = "Schedule I"
#: Steam beta branch that ships the alternate Mono build of the game.
MONO_BRANCH = "alternate"

#: UI languages (codes) and colour themes offered in Settings.
SUPPORTED_LANGUAGES = ("en", "es", "fr", "de", "pt", "ru", "zh", "ja")
SUPPORTED_THEMES = ("dark", "light", "midnight", "amber", "rose", "ocean")

#: Directories inside the game folder that MelonLoader consumes.
MOD_DIRS = ("Mods", "Plugins", "UserLibs")
DISABLED_SUFFIX = ".disabled"
SIDECAR_SUFFIX = ".modinfo.json"

SAFE_LAUNCH_METHODS = ("steam", "direct")


class Backend(str, Enum):
    """Unity scripting backend used by the installed game build."""

    IL2CPP = "il2cpp"
    MONO = "mono"
    UNKNOWN = "unknown"


class LaunchMethod(str, Enum):
    """How the launcher starts the game."""

    STEAM = "steam"
    DIRECT = "direct"


class ModKind(str, Enum):
    """Which MelonLoader folder a mod file belongs to."""

    MOD = "mod"
    PLUGIN = "plugin"
    USERLIB = "userlib"

    @classmethod
    def from_dir_name(cls, name: str) -> "ModKind":
        lowered = name.lower()
        if lowered == "plugins":
            return cls.PLUGIN
        if lowered == "userlibs":
            return cls.USERLIB
        return cls.MOD


# ---------------------------------------------------------------------------
# Game / loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoaderStatus:
    """Result of inspecting the game folder for MelonLoader."""

    installed: bool = False
    version: str | None = None
    proxy_dll: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass
class GameInstall:
    """A validated (or partially validated) Schedule I installation."""

    root: Path
    exe: Path
    backend: Backend = Backend.UNKNOWN
    unity_version: str | None = None
    build_id: str | None = None
    last_updated: str | None = None
    steam_library: str | None = None
    save_dir: Path | None = None
    loader: LoaderStatus = field(default_factory=LoaderStatus)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.exe.is_file()

    @property
    def game_dir(self) -> Path:
        return self.root


@dataclass(frozen=True)
class LoaderRelease:
    """A MelonLoader release asset that we can download."""

    version: str
    asset_name: str
    download_url: str
    page_url: str
    size_bytes: int | None = None
    sha256: str | None = None


# ---------------------------------------------------------------------------
# Mods / profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModFile:
    """One enabled or disabled mod file on disk."""

    path: Path
    rel: str
    name: str
    version: str | None = None
    author: str | None = None
    kind: ModKind = ModKind.MOD
    enabled: bool = True
    size: int = 0
    source: str = "unknown"  # sidecar | filename | unknown
    dependencies: tuple[str, ...] = ()
    description: str | None = None
    #: Where the mod came from (Thunderstore download URL, archive name, …).
    origin: str | None = None

    @property
    def mod_id(self) -> str:
        """Stable identifier: relative path without the ``.disabled`` suffix."""
        return self.rel

    @property
    def display_author(self) -> str:
        return self.author or "—"

    @property
    def display_version(self) -> str:
        return self.version or "—"


@dataclass
class Profile:
    """A named set of disabled mod ids. Everything not listed is enabled."""

    name: str
    disabled: set[str] = field(default_factory=set)
    #: Save slot folder name linked to this profile, if any.
    save_slot: str | None = None

    def is_enabled(self, mod_id: str) -> bool:
        return mod_id not in self.disabled

    def set_enabled(self, mod_id: str, enabled: bool) -> None:
        if enabled:
            self.disabled.discard(mod_id)
        else:
            self.disabled.add(mod_id)


@dataclass
class AppConfig:
    """Persisted application configuration (JSON in %APPDATA%)."""

    game_path: str | None = None
    launch_method: str = LaunchMethod.STEAM.value
    language: str = "en"
    theme: str = "dark"
    active_profile: str = "Default"
    profiles: dict[str, Profile] = field(default_factory=dict)
    extra_args: str = ""
    #: Launcher-only display names for save slots, keyed by folder name.
    save_labels: dict[str, str] = field(default_factory=dict)
    schema_version: int = 1

    def ensure_defaults(self) -> None:
        if self.launch_method not in SAFE_LAUNCH_METHODS:
            self.launch_method = LaunchMethod.STEAM.value
        if self.language not in SUPPORTED_LANGUAGES:
            self.language = "en"
        if self.theme not in SUPPORTED_THEMES:
            self.theme = "dark"
        if not self.profiles:
            self.profiles = {"Default": Profile("Default")}
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles))
        # Normalise names and keep profile names as their own keys.
        self.profiles = {p.name: p for p in self.profiles.values()}
        if not self.extra_args:
            self.extra_args = ""

    @property
    def active(self) -> Profile:
        return self.profiles[self.active_profile]


@dataclass(frozen=True)
class BackupInfo:
    """Metadata for a ZIP backup stored in the app data folder."""

    id: str
    label: str
    kind: str
    created_at: str
    size_bytes: int
    base_dir: str
    path: Path


@dataclass
class ImportReport:
    """Outcome of importing one or more mod archives."""

    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_loader: bool = False


# ---------------------------------------------------------------------------
# Thunderstore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThunderstoreVersion:
    name: str
    full_name: str
    version_number: str
    description: str
    dependencies: tuple[str, ...]
    download_url: str
    file_size: int | None = None
    website_url: str = ""


@dataclass(frozen=True)
class ThunderstorePackage:
    namespace: str
    name: str
    full_name: str
    description: str
    latest: ThunderstoreVersion
    versions: tuple[ThunderstoreVersion, ...] = ()
    total_downloads: int | None = None
    is_deprecated: bool = False

    @property
    def icon_url(self) -> str:
        return ""


@dataclass(frozen=True)
class ResolvedPackage:
    package: ThunderstorePackage
    version: ThunderstoreVersion
    requested_by: str | None = None
