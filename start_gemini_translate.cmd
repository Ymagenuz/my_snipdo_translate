@echo off
setlocal

set "APP_DIR=%~dp0"
set "PYTHONW=%APP_DIR%.venv\Scripts\pythonw.exe"
set "SCRIPT=%APP_DIR%gemini_translate.pyw"

if not exist "%PYTHONW%" (
    echo Missing virtual environment Python:
    echo %PYTHONW%
    echo.
    echo Create the environment first:
    echo python -m venv .venv
    echo .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo Missing script:
    echo %SCRIPT%
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%SCRIPT%" %*
