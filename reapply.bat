@echo off
cd /d "%~dp0"

if not exist "python\python.exe" goto missing
if not exist "atlas" goto missing

echo Putting your saved voice line changes back...
echo.
python\python.exe serve.py --atlas atlas --reapply %*
echo.
echo Done. Press any key to close.
pause >nul
exit /b 0

:missing
echo.
echo   Chatterbox cannot start: files are missing.
echo.
echo   Two things cause this.
echo.
echo   1. You downloaded the source code instead of the release.
echo      The green "Code" button on GitHub gives you the source, which does
echo      not include Python or the voice line data. Get the zip from the
echo      Releases page instead.
echo.
echo   2. You have not extracted the zip, or only extracted part of it.
echo      Extract the WHOLE zip to a real folder first. You cannot run this
echo      from inside the zip file.
echo.
echo   Next to run.bat you should see folders named: python, atlas, tools
echo.
pause
exit /b 1
