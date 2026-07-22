@echo off
rem ---------------------------------------------------------------------
rem  KMARKET dashboard launcher.
rem
rem  THIS FILE MUST STAY PURE ASCII. Do not put Russian text here.
rem  cmd.exe parses a .bat using the console code page (cp866 on a Russian
rem  Windows), so UTF-8 text is read as garbage; and "chcp 65001" inside
rem  the file shifts byte offsets mid-parse, which breaks if/else blocks
rem  and leaks their echo lines out as commands. All human-facing text
rem  lives in Python (kmarket/web/__main__.py), which writes to the
rem  console through the Unicode API and does not care about code pages.
rem ---------------------------------------------------------------------
title KMARKET
cd /d "%~dp0"

python -m kmarket.web
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" (
    echo   Exit code %EXITCODE%. If Python complains about missing modules:
    echo       pip install -r requirements.txt
    echo.
)
pause
