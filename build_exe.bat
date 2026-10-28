@echo off
setlocal
cd /d "%~dp0"

echo Installing runtime and packaging tools...
py -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo Building the Windows executable...
rem Absolute paths: with --specpath, PyInstaller resolves relative --add-data/
rem and --icon paths against build\ instead of the project root.
py -m PyInstaller --noconfirm --clean --windowed --onefile --name ScheduleForge ^
  --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.png;assets" ^
  --distpath "%~dp0dist" --workpath "%~dp0build\work" --specpath "%~dp0build" "%~dp0main.py"
if errorlevel 1 goto :error

rem Fail loudly if the distributable is incomplete.
if not exist "dist\ScheduleForge.exe" goto :error

rem Keep dist\ down to the single executable: remove leftovers from older builds.
if exist "dist\ScheduleForge" rmdir /s /q "dist\ScheduleForge"
if exist "dist\ScheduleIModLauncher.exe" del /q "dist\ScheduleIModLauncher.exe"
if exist "dist\ScheduleIModLauncher" rmdir /s /q "dist\ScheduleIModLauncher"

echo.
echo Build complete. The release is a single file:
echo   %CD%\dist\ScheduleForge.exe
echo You can move or copy that file anywhere; it does not need other files.
rem Keep the window open when the script is double-clicked.
pause
exit /b 0

:error
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
