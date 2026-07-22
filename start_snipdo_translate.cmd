@echo off
setlocal

set "EXE=%~dp0SnipDoTranslate.exe"

if not exist "%EXE%" (
    >&2 echo SnipDoTranslate.exe was not found next to this launcher.
    exit /b 1
)

start "" "%EXE%" %*
exit /b %ERRORLEVEL%
