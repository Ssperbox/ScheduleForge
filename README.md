# Schedule Forge

A local, desktop mod manager for **Schedule I** (TVGS, Steam AppID `3164500`).
It detects the game (including libraries on other drives), installs/repairs
MelonLoader, imports mods, manages enabled/disabled state and profiles, searches
Thunderstore, backs up files before changes, and launches the game.

**Platform:** Windows 10/11 (primary). **Stack:** Python 3.11+ + PySide6.
**No telemetry. No bundled mods.**

> Unofficial community tool. Not affiliated with TVGS or Valve/Steam.
> Mods are third-party code: install only from authors you trust.

---

## 1. Research summary (Phase 1)

### 1.1 The game

| Item | Finding |
|---|---|
| Engine | Unity 2022 (PCGamingWiki; Unity version can change between updates) |
| Default backend | **IL2CPP** |
| Alternative | **Mono** build on the optional Steam beta branch **`alternate`** (community sources; one also mentions `alternate-beta`) |
| Steam AppID | `3164500` (`Schedule I: Free Sample` is a separate app, `3205720`) |
| Install folder | `steamapps\common\Schedule I` (libraries can be on any drive) |
| Executable | `Schedule I.exe` |
| Saves | `%USERPROFILE%\AppData\LocalLow\TVGS\Schedule I\Saves\<user-id>\` |

The app detects IL2CPP vs Mono from the installed files
(`GameAssembly.dll` vs `Schedule I_Data\Managed\Assembly-CSharp.dll`) rather than
from a branch name, so it does not depend on the exact beta branch label.

### 1.2 Mod loader: MelonLoader

MelonLoader is the de-facto loader for Schedule I. BepInEx exists in the wider
Unity ecosystem but is not the standard path for this game; the launcher skips
BepInEx packages with a clear warning instead of pretending they work.

- **Install:** extract the official `MelonLoader.x64.zip` into the game folder.
  The archive contains `MelonLoader/` and a proxy DLL (`version.dll` and
  alternates such as `winhttp.dll`, `winmm.dll`, …). The proxy makes the loader
  start with the game, whether Steam or the executable launches it.
- **Folders:** `Mods/` (mods), `Plugins/` (plugins), `UserLibs/` (libraries),
  `UserData/` (config/data), plus `MelonLoader/` (loader files and logs).
- **Runtime:** IL2CPP support requires the **.NET 6 Desktop Runtime**. The
  official MelonLoader *installer* can install it; this app uses the portable
  ZIP, so it only **warns** that the runtime must be present.
- **Version compatibility:** MelonLoader and the game both update. The app
  installs the latest stable release by default and offers “Install from local
  ZIP” for pinning an older version. Mono-branch mods are not interchangeable
  with IL2CPP mods.
- **Uninstall:** delete the proxy DLL and `MelonLoader/`; optionally back up and
  remove `Mods/`, `Plugins/`, `UserLibs/`, `UserData/`. This app backs up first.

### 1.3 Distribution and formats

- **Thunderstore** community: `https://thunderstore.io/c/schedule-i/`. Public
  JSON API (v1 community listing and experimental endpoints), plus direct ZIP
  downloads. Package roots contain `manifest.json`, `README.md`, `icon.png` and
  loader-routed folders. Dependencies are strings like
  `Author-Package-1.0.0`.
- **Nexus Mods**: Schedule I section exists (`schedule1`). No official public
  download API is used here; users can add manually downloaded `.zip`/`.dll`
  files.
- **GitHub Releases**: some authors publish there; supported via “Add mod”.
- **Terms/limits:** Thunderstore has no verified official numeric rate limit in
  the sources read. The app caches the package index (15 min) and only downloads
  on explicit user action. Respect each author’s licence; do not redistribute.

### 1.4 Existing managers (what we borrowed)

- **r2modman / Thunderstore Mod Manager:** profile-based, download requested
  mods with dependencies, and launch the selected profile. Public docs do not
  specify one universal deployment mechanism for every game.
- **Gale:** documents linking mod files instead of copying to save space.
- **Vortex:** hardlink deployment by default (where supported), with purge.
- **Our choice:** profiles store disabled mod ids; activation renames
  `Foo.dll` ↔ `Foo.dll.disabled`. No copies, no symlinks, no hardlinks, works on
  every Windows filesystem, and a single game folder is always in a known state.
  Trade-off: only one profile is deployed at a time (same practical model as
  clicking “Start modded” in r2modman).

### 1.5 Detection and launching

- **Steam roots:** `HKCU\Software\Valve\Steam` → `SteamPath` and
  `HKLM\SOFTWARE\Valve\Steam` → `InstallPath` (both registry views), plus common
  default folders.
- **Libraries:** `<Steam>\steamapps\libraryfolders.vdf` and the newer
  `<Steam>\config\libraryfolders.vdf`; both legacy string values and modern
  `{ "path": ... }` objects are parsed.
- **Game:** `<library>\steamapps\appmanifest_3164500.acf` → `installdir` and
  `buildid`, then `<library>\steamapps\common\<installdir>`.
- **Validation:** executable + `Schedule I_Data` + a known scripting backend.
- **Launch:** `steam://rungameid/3164500` (default, keeps Steamworks/playtime
  normal) or the executable directly (Steam must be running for Steamworks).
  The loader proxy loads in both cases. Extra arguments are supported only for
  direct launch.

### 1.6 Open questions / uncertainty

- The beta branch is documented as `alternate`; `alternate-beta` also appears in
  community material. The live Steam branch dropdown was not verified.
- Valve’s Steam browser-protocol page was blocked by anti-bot during research, so
  the exact `steam://rungameid` semantics were not independently verified.
- Thunderstore’s current Terms of Service page could not be retrieved, and no
  official numeric rate limit was found. The client is deliberately conservative:
  cache, low concurrency, user-initiated downloads.
- r2modman’s exact per-game enable/disable implementation was not verified.
- There is no reliable, machine-readable “this mod supports game build X” field,
  so the app warns about backend and loader mismatch but cannot guarantee mod
  compatibility.

### Sources read

- [Schedule I on Steam](https://store.steampowered.com/app/3164500/Schedule_I/)
- [PCGamingWiki — Schedule I](https://www.pcgamingwiki.com/wiki/Schedule_I)
- [Steam discussion quoting the Mono/alternate build notes](https://steamcommunity.com/app/3164500/discussions/1/599648970674834418/)
- [Steam discussion — save location](https://steamcommunity.com/app/3164500/discussions/1/599648681388413518/)
- [Thunderstore — Schedule I community](https://thunderstore.io/c/schedule-i/)
- [Thunderstore — MelonLoader package](https://thunderstore.io/c/schedule-i/p/LavaGang/MelonLoader/)
- [LavaGang/MelonLoader](https://github.com/LavaGang/MelonLoader)
- [MelonLoader releases](https://github.com/LavaGang/MelonLoader/releases/latest)
- [MelonLoader Installer](https://github.com/LavaGang/MelonLoader.Installer)
- [MelonEnvironment paths](https://github.com/LavaGang/MelonLoader/blob/master/MelonLoader/Utils/MelonEnvironment.cs)
- [Thunderstore API schema](https://thunderstore.io/api/docs/)
- [Thunderstore backend README](https://github.com/thunderstore-io/Thunderstore/blob/master/README.md)
- [Thunderstore — creating a package](https://wiki.thunderstore.io/mods/creating-a-package)
- [Thunderstore — packaging by loader](https://wiki.thunderstore.io/mods/packaging-your-mods)
- [r2modman](https://github.com/ebkr/r2modmanPlus)
- [Gale features](https://github.com/Kesomannen/gale/wiki/Features)
- [Vortex deployment methods](https://github.com/Nexus-Mods/Vortex/wiki/MODDINGWIKI-Users-General-Deployment-Methods)
- [BepInEx Steam/Proton interop](https://docs.bepinex.dev/master/articles/advanced/steam_interop.html)
- [UnityDoorstop wiki](https://github.com/NeighTools/UnityDoorstop/wiki)
- [steam-game-path (registry/library behavior)](https://github.com/osztenkurden/steam-game-path)
- [Valkyrie issue on Steam paths](https://github.com/NPBruce/valkyrie/issues/1056)
- [Nexus Mods — Schedule I](https://www.nexusmods.com/games/schedule1/mods)

**Live verification performed on 2026-10-28** (not part of the automated tests):

- Steam roots/libraries were detected correctly on this machine (2 libraries).
- MelonLoader **0.7.3** official asset was downloaded; its published SHA-256 was
  verified; the real archive contains `MelonLoader/` + `version.dll`; installing
  it into a fake game folder and detecting it worked.
- Thunderstore returned **581** Schedule I packages; dependency resolution
  worked; a real package was downloaded through the CDN and imported into
  `Mods/` with metadata. The downloaded test package was then removed.
- Schedule I itself is **not installed on the development machine**, so the
  actual game launch and in-game mod loading could not be exercised here.
  Launching is implemented through standard `steam://rungameid` / `Popen` calls
  and is documented as such.

---

## 2. Design (Phase 2)

### 2.1 Stack choice

**Python 3.11+ + PySide6 (Qt 6)**, packaged with PyInstaller.

Why this and not C#/WPF, Tauri, or Electron:

- Native widgets, tables, dialogs, drag-and-drop, HiDPI and dark/light palettes
  with one dependency.
- No web runtime and no Node toolchain; the whole app is auditable Python.
- PyInstaller produces a Windows `.exe` without requiring a .NET SDK on the
  build machine.
- C#/WPF would be more native but needs a separate SDK/build pipeline; Tauri and
  Electron add a webview/Node stack for what is a fairly small desktop tool.

The core is pure Python and Qt-free, so detection/loader/mod/backup logic can be
tested and reused from a CLI.

### 2.2 Architecture

```
main.py
  └─ launcher/ui/main_window.py          PySide6 UI only
        ├─ workers.py  (QThread tasks)    I/O never blocks the UI
        └─ launcher/core/                 pure Python, no Qt
              ├─ models.py                typed dataclasses/enums
              ├─ config.py                %APPDATA% JSON + paths
              ├─ vdf.py                   KeyValues parser
              ├─ steam_detect.py          registry, libraries, manifests
              ├─ game.py                  validation, backend, saves, version
              ├─ archive.py               zip-slip-safe extraction
              ├─ loader.py                MelonLoader install/repair/uninstall
              ├─ mod_mgr.py               scan, toggle, import, delete, profile import/export,
              │                           loader toggle, backend mismatch check
              ├─ backup.py                ZIP backups + safety restore
              ├─ thunderstore.py          index, search, dependencies, download, update check
              ├─ net.py                   HTTPS allow-list + size/hash checks
              └─ launch.py                Steam URI / direct executable
```

### 2.3 Project structure

```
main.py
requirements.txt
build_exe.bat
README.md
launcher/
  __init__.py
  core/
    __init__.py  models.py  config.py  vdf.py  platform_win.py
    steam_detect.py  game.py  archive.py  backup.py  net.py
    loader.py  mod_mgr.py  thunderstore.py  launch.py
  ui/
    __init__.py  i18n.py  theme.py  workers.py  widgets.py
    dialogs.py  main_window.py
tests/
  test_core.py
  test_ui_smoke.py
```

### 2.4 Data model and storage

Configuration is stored in `%APPDATA%\ScheduleIModLauncher\config.json`
(not next to the .exe, so it survives updates and Program Files permissions):

```json
{
  "schema": 1,
  "game_path": "C:\\Games\\Steam\\steamapps\\common\\Schedule I",
  "launch_method": "steam",
  "language": "en",
  "theme": "dark",
  "active_profile": "Default",
  "extra_args": "",
  "profiles": {
    "Default": { "disabled": [] },
    "Vanilla": { "disabled": ["Mods/ExampleMod.dll"] }
  }
}
```

Profiles store **disabled mod ids**; everything else is enabled. A mod id is its
game-relative path without the `.disabled` suffix.

A profile exported from the app is a separate JSON file:

```json
{
  "format": "schedule-i-launcher-profile",
  "version": 1,
  "name": "Vanilla",
  "disabled": ["Mods/ExampleMod.dll"],
  "save_slot": null
}
```

Imported mods get a sidecar next to the DLL (for example
`Mods/ExampleMod.modinfo.json`):

```json
{
  "name": "ExampleMod",
  "version": "1.2.3",
  "author": "AuthorName",
  "dependencies": ["Author-Library-1.0.0"],
  "description": "…",
  "origin": "https://thunderstore.io/package/download/…",
  "installed_at": "2026-10-28T12:00:00+00:00"
}
```

Manually copied DLLs have no sidecar; name/version are inferred from the file
name when possible (and shown as such, not invented).

Other app data:

```
%APPDATA%\ScheduleIModLauncher\
  config.json
  logs\launcher.log
  backups\<timestamp>-<label>.zip      (manifest + files/)
  cache\melonloader_release.json       (6 h cache)
  cache\downloads\MelonLoader-<ver>.zip
  cache\thunderstore\index-schedule-i.json
  cache\thunderstore\downloads\<Author>-<Mod>-<ver>.zip
```

### 2.5 User flow

1. **Start** → load saved path; otherwise scan Steam libraries. If several
   installs exist, ask which one to use. Manual “Browse…” validates immediately.
2. **Status** → backend (IL2CPP/Mono), Unity version, Steam build id, loader
   presence/version, proxy DLL, save folder, warnings.
3. **Loader** → “Install / repair” downloads the official release (SHA-256 when
   GitHub publishes it), backs up existing loader files, extracts safely.
   “Install from local ZIP” supports offline/pinned versions. Uninstall offers to
   keep or remove mod folders, with a backup first.
4. **Add mods** → button or drag-and-drop `.dll`/`.zip`; archives are extracted
   in a staging folder, routed to `Mods`/`Plugins`/`UserLibs`/`UserData`, and
   overwritten files are backed up.
5. **Get mods** → search Thunderstore; resolve dependencies; show exactly what
   will be installed; download from Thunderstore on confirmation; import.
6. **Toggles/profiles** → checkboxes rename files; the active profile records the
   state. Switching profiles applies renames (game must be closed). Profiles can be
   exported to and imported from a JSON file. “Disable loader” renames the MelonLoader
   proxy DLLs to `.disabled` for a vanilla run and “Enable loader” restores them;
   nothing is deleted. “Check updates” compares installed Thunderstore mods with
   the latest versions and reports any updates. Mods whose IL2CPP/Mono hint does
   not match the installed game are marked “Wrong backend”.
7. **Play** → apply active profile, then `steam://rungameid/3164500` or direct
   executable; poll process state and report start/exit in the log/status.
8. **Backups** → automatic before loader/mod changes; manual whole-mod-folder
   backup and save backup; restore creates a pre-restore safety backup.

### 2.6 Security and quality decisions

- **Backups first:** loader install/uninstall, imports that overwrite, mod
  deletion, and restores all create ZIP backups in `%APPDATA%`.
- **Zip slip:** every archive extraction uses `archive.extract_zip`, rejecting
  absolute paths, drive letters, `..`, NUL bytes, symlinks, and oversized or
  zip-bomb-like archives. This is covered by tests.
- **Downloads:** only HTTPS, host allow-lists (`github.com`/GitHub CDN for the
  loader, `thunderstore.io` for mods), size caps, expected sizes, loader SHA-256
  when published, and package manifest identity checks for Thunderstore.
- **No telemetry:** no analytics, no crash reporting, no account. Network calls
  happen only for explicit loader/mod actions.
- **No bundled mods:** the app ships no third-party content and does not reupload
  anything.
- **No silent deletion of game files:** uninstall only touches MelonLoader’s
  proxy/loader files and, if explicitly ticked, its mod folders. Original game
  files are never modified.
- **Errors:** permission failures, missing folders, offline state, rate limits,
  corrupt archives, running game, and invalid config are surfaced as messages
  and logged; the UI stays responsive via worker threads.

---

## 3. Run it

### Requirements

- Windows 10/11 (primary).
- Python **3.11+** (tested with 3.13).
- Internet only for loader/mod downloads.

### Steps (PowerShell / cmd)

```bat
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Or with an activated shell:

```bat
py -m venv .venv
.\.venv\Scripts\activate
py -m pip install -r requirements.txt
py main.py
```

If you use PowerShell and activation is blocked, call the venv interpreter
directly as in the first block.

### Tests

```bat
set QT_QPA_PLATFORM=offscreen
py -m unittest discover -s tests -v
```

PowerShell: `$env:QT_QPA_PLATFORM="offscreen"` before the test command.
Git Bash: `QT_QPA_PLATFORM=offscreen py -m unittest discover -s tests -v`.

The suite (35 tests on 2026-10-28) covers VDF/ACF parsing, game validation/backend
detection, zip-slip rejection, mod scan/toggle/profile/import/remove, loader
install/detect/uninstall, config round-trip, backup/restore, Thunderstore
parsing/dependency order, and a headless UI smoke test (window construction, game
load, checkbox rename, language/theme switching).

---

## 4. Package as an .exe

Single-file build (the release artifact):

```bat
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name ScheduleForge --icon ..\assets\icon.ico --add-data "..\assets\icon.png;assets" ^
  --distpath dist --workpath build\work --specpath build main.py
```

Or run `build_exe.bat`, which does the same and clears any leftover one-folder
build from `dist\`.

Result: `dist\ScheduleForge.exe`. That file is self-contained: Python,
Qt and the app resources are inside it, so you can move or copy it anywhere and
upload just that file as a GitHub release asset.

The executable still stores config, backups and caches in
`%APPDATA%\ScheduleIModLauncher`, not next to the `.exe`. There is no code
signing; Windows SmartScreen may warn about an unsigned executable. The first
launch is a little slower than a one-folder build because it unpacks to a temp
folder.

---

## 5. Known limitations and possible improvements

**Limitations**

- Schedule I was not installed on the development machine, so real game launch
  and in-game mod loading were not exercised. Loader ZIP installation, detection,
  process lookup and Thunderstore downloads were verified.
- The portable MelonLoader ZIP does **not** install the .NET 6 Desktop Runtime.
  IL2CPP users must have it installed; the UI warns.
- No compatibility matrix for mod ↔ game build ↔ loader version. The app warns
  about IL2CPP/Mono and missing loaders, but cannot validate a mod’s target build.
- Metadata for manually copied DLLs is inferred from the filename (or shown as
  unknown). Only imports made by this app get full sidecar metadata.
- Nexus Mods is not integrated (no public API used); use “Add mod” with manual
  downloads.
- Thunderstore dependencies are resolved within the Schedule I community.
  Loader packages are handled as installation notes, not installed as mods.
- Only one profile is deployed to disk at a time; toggling profiles renames
  files and requires the game to be closed.
- BepInEx packages are skipped with a warning (this game’s standard loader is
  MelonLoader).
- UI translation covers interface strings; technical/core log lines remain in
  English for searchability.
- Windows is the target. Core modules have non-Windows fallbacks for development,
  but the UI was only tested on Windows (headless Qt).
- Mod update checks compare against the Thunderstore version list only for mods
  installed from Thunderstore (those with a recorded origin). They report updates
  and do not install them. No modpack format, no save editor.
- Profile export/import uses this app's own JSON format (name, disabled mods, save
  slot); it is not r2modman-compatible.
- Restoring a backup overwrites same-named files (after a safety backup); it does
  not delete extra files that were added after the backup.
- Loader uninstall may remove proxy DLLs such as `version.dll`; they are backed
  up first, and proxies are left alone if a `BepInEx` folder is present.

**Good next steps**

- Install available mod updates from the update check (currently report only).
- Add r2modman-compatible profile import/export and modpack support.
- Add a save snapshot reminder and a “restore vanilla saves” helper.
- Add a per-mod “open author page” button and richer metadata (icon, downloads).
- Add an optional self-update check for the launcher itself (no telemetry).
- Add signed builds and an installer.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| “Invalid game folder” | Pick the folder containing `Schedule I.exe` and `Schedule I_Data` (Steam → Manage → Browse local files). |
| Game not auto-detected | Steam may be installed in an unusual place or the game copied elsewhere. Use **Browse…**; the path is saved. |
| Mods don’t load | Install/repair MelonLoader, check `MelonLoader/Latest.log`, make sure the proxy DLL (`version.dll` by default) is present, and confirm the .NET 6 Desktop Runtime for IL2CPP. |
| Mono mods don’t load | Switch Schedule I to the `alternate` Steam beta and use Mono-compatible mods; IL2CPP mods are not interchangeable. |
| “Game is running” when changing mods | Close the game; renaming DLLs while it runs is blocked to avoid corrupting the session. |
| Thunderstore fails | Check the connection; cached listings still work when the API is unreachable. Downloads always come from thunderstore.io. |
| Permission error | Avoid protected folders; run the launcher with sufficient rights or move the Steam library to a writable drive. Never run untrusted mods as administrator. |
| Still says “Game running” after closing | Windows can keep an exited process listed with status `Unknown` (a zombie). The detector ignores those, and direct launches are reaped in the background. If a real `Running`/`Not Responding` process remains, end it in Task Manager. |
| Loader uninstall kept proxy DLLs | A BepInEx folder was detected; proxy DLLs may be shared, so they are left in place and backed up. |
| `Failed to load Python DLL ...\build\work\...` | You launched the PyInstaller intermediate, not the app. Run `dist\ScheduleForge.exe`. Re-run `build_exe.bat` to clear old builds. |

---

## 7. File-by-file map

| Path | Responsibility |
|---|---|
| `main.py` | Qt application bootstrap, log file, exception hook. |
| `launcher/core/models.py` | Typed models/enums and constants (AppID, folders, paths). |
| `launcher/core/config.py` | `%APPDATA%` paths, atomic JSON config, file logging. |
| `launcher/core/vdf.py` | Tolerant KeyValues parser for Steam `.vdf`/`.acf`. |
| `launcher/core/platform_win.py` | Registry, `tasklist`, shell open, Windows checks. |
| `launcher/core/steam_detect.py` | Steam roots, libraries, app manifests, `steam://` URI. |
| `launcher/core/game.py` | Path validation, backend/Unity version, save dirs, warnings. |
| `launcher/core/archive.py` | Zip-slip-safe staged extraction and manifest reading. |
| `launcher/core/backup.py` | ZIP backups, manifests, safe restore, cleanup. |
| `launcher/core/net.py` | HTTPS allow-lists, size caps, download hashing. |
| `launcher/core/loader.py` | MelonLoader detect/install/repair/uninstall/marker. |
| `launcher/core/mod_mgr.py` | Scan, `.disabled` toggles, import routing, sidecars, delete. |
| `launcher/core/thunderstore.py` | Index cache, search, dependency resolution, downloads. |
| `launcher/core/launch.py` | Steam/direct launch and argument parsing. |
| `launcher/ui/i18n.py` | English/Spanish UI strings. |
| `launcher/ui/theme.py` | Dark/light Qt stylesheets and palette. |
| `launcher/ui/workers.py` | `QThread` task wrapper. |
| `launcher/ui/widgets.py` | Cards, badges, log view, drag-and-drop table. |
| `launcher/ui/dialogs.py` | Dependency confirmation, loader uninstall dialog. |
| `launcher/ui/main_window.py` | All screens, state refresh, actions, polling. |
| `tests/test_core.py` | Core logic and security tests. |
| `tests/test_ui_smoke.py` | Headless Qt smoke tests. |
| `build_exe.bat` | Reproducible PyInstaller single-file build with the app icon. |
| `assets/make_icon.py` | Regenerates `assets/icon.png` and `assets/icon.ico` with Pillow. |
