@echo off
setlocal enabledelayedexpansion

REM Ensure the script works relative to its own directory
cd /d "%~dp0"

REM Try to detect Python via the "py" launcher first
set "PYTHON_EXE="
set "PYTHON_SOURCE="
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    set "PYTHON_EXE=%%i"
    set "PYTHON_SOURCE=py launcher"
)

REM Fall back to scanning the PATH for python.exe if the launcher is unavailable
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        echo %%i ^| findstr /I "inkscape" >nul
        if errorlevel 1 (
            set "PYTHON_EXE=%%i"
            set "PYTHON_SOURCE=PATH"
            goto AFTER_PY_DETECT
        )
    )
)

:AFTER_PY_DETECT

if not defined PYTHON_EXE (
    call :LOCATE_OFFICIAL_PYTHON
)

if not defined PYTHON_EXE (
    echo Не удалось найти подходящий интерпретатор Python.
    echo Установите официальную версию Python с сайта https://www.python.org/downloads/ и убедитесь,
    echo что при установке была отмечена опция "Add Python to PATH".
    echo.
    goto END
)

set "EMBEDDED_PYTHON="
echo !PYTHON_EXE! ^| findstr /I "inkscape" >nul
if not errorlevel 1 (
    set "EMBEDDED_PYTHON=1"
)

if defined EMBEDDED_PYTHON (
    set "ORIGINAL_PYTHON=!PYTHON_EXE!"
    call :LOCATE_OFFICIAL_PYTHON
    if defined PYTHON_EXE (
        echo Обнаружена установленная версия Python, которая подходит для установки зависимостей:
        echo     !PYTHON_EXE!
        set "PYTHON_SOURCE=auto-detected default install"
        set "EMBEDDED_PYTHON="
    ) else (
        set "PYTHON_EXE=!ORIGINAL_PYTHON!"
    )
)

if defined EMBEDDED_PYTHON (
    echo Обнаружено, что найденный Python встроен в стороннее приложение.
    echo Такой Python не поставляется с готовыми колёсами для зависимостей вроде aiohttp, поэтому установка завершается ошибкой.
    echo.
    echo Установите официальную версию Python с сайта https://www.python.org/downloads/, отметьте опцию "Add Python to PATH"
    echo и запустите этот скрипт заново. Тогда виртуальное окружение будет создано автоматически.
    echo.
    goto END
)

echo Используется интерпретатор Python: !PYTHON_EXE!
if /I "!PYTHON_SOURCE!"=="PATH" (
    echo (обнаружен через команду "where python")
) else if /I "!PYTHON_SOURCE!"=="auto-detected default install" (
    echo (обнаружен в стандартной директории установки python.org)
) else if /I "!PYTHON_SOURCE!"=="py launcher" (
    echo (обнаружен через команду "py -3")
)
echo.

REM Create a virtual environment if it does not exist
if not exist .venv (
    echo Создаю виртуальное окружение...
    "!PYTHON_EXE!" -m venv .venv
    if errorlevel 1 goto END
)

REM Activate the virtual environment
call .venv\Scripts\activate.bat
if errorlevel 1 goto END

REM Upgrade pip and install dependencies inside the virtual environment
python -m pip install --upgrade pip
if errorlevel 1 goto END

python -m pip install -r requirements.txt
if errorlevel 1 goto END

REM Run the bot
python Main.py

goto END

:LOCATE_OFFICIAL_PYTHON
set "OFFICIAL_PYTHON="
if defined LocalAppData (
    for /f "delims=" %%d in ('dir /b /ad /o-n "%LocalAppData%\Programs\Python" 2^>nul') do (
        if "!OFFICIAL_PYTHON!"=="" (
            if exist "%LocalAppData%\Programs\Python\%%d\python.exe" (
                set "OFFICIAL_PYTHON=%LocalAppData%\Programs\Python\%%d\python.exe"
            )
        )
    )
)

if "!OFFICIAL_PYTHON!"=="" if defined ProgramFiles (
    for /f "delims=" %%d in ('dir /b /ad /o-n "%ProgramFiles%\Python*" 2^>nul') do (
        if "!OFFICIAL_PYTHON!"=="" (
            if exist "%ProgramFiles%\%%d\python.exe" (
                set "OFFICIAL_PYTHON=%ProgramFiles%\%%d\python.exe"
            )
        )
    )
)

if not "!OFFICIAL_PYTHON!"=="" (
    set "PYTHON_EXE=!OFFICIAL_PYTHON!"
    set "PYTHON_SOURCE=auto-detected default install"
)

exit /b

:END
echo.
pause