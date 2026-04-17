@echo off
REM ================================================================
REM  Albion GPS - INSTALLATION (a lancer une seule fois)
REM  Installe Python, Npcap et les dependances automatiquement.
REM ================================================================
title Albion GPS - Installation
cd /d "%~dp0"

echo.
echo  ============================================
echo   ALBION GPS - INSTALLATION
echo  ============================================
echo.

REM ================================================================
REM  1. PYTHON
REM ================================================================
echo [1/4] Verification de Python...
where python >nul 2>&1
if errorlevel 1 (
    echo       Python non trouve. Telechargement...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe', '%TEMP%\python_install.exe')"
    if not exist "%TEMP%\python_install.exe" (
        echo       [ERREUR] Telechargement echoue.
        echo       Installe Python manuellement : https://www.python.org/downloads/
        echo       COCHE "Add python.exe to PATH" pendant l'installation !
        echo.
        pause
        exit /b 1
    )
    echo       Lancement de l'installeur Python...
    echo       IMPORTANT : COCHE "Add python.exe to PATH" en bas de la fenetre !
    echo.
    "%TEMP%\python_install.exe"
    echo.
    echo       Si tu as installe Python, ferme cette fenetre,
    echo       REDEMARRE TON PC, puis relance install.bat.
    echo.
    pause
    exit /b 0
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo       %%v - OK
)

REM ================================================================
REM  2. NPCAP (necessaire pour la detection de zone)
REM ================================================================
echo.
echo [2/4] Verification de Npcap...
if not exist "%SystemRoot%\System32\Npcap\wpcap.dll" (
    echo       Npcap non trouve. Telechargement...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://npcap.com/dist/npcap-1.80.exe', '%TEMP%\npcap_install.exe')"
    if not exist "%TEMP%\npcap_install.exe" (
        echo       [ERREUR] Telechargement echoue.
        echo       Installe Npcap manuellement : https://npcap.com/#download
        echo.
        pause
        exit /b 1
    )
    echo       Lancement de l'installeur Npcap...
    echo       COCHE "Install Npcap in WinPcap API-compatible Mode" !
    echo.
    "%TEMP%\npcap_install.exe"
    echo.
) else (
    echo       Npcap - OK
)

REM ================================================================
REM  3. ENVIRONNEMENT VIRTUEL
REM ================================================================
echo.
echo [3/4] Creation de l'environnement Python...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo       [ERREUR] Impossible de creer le venv.
        echo       Verifie que Python est installe et dans le PATH.
        pause
        exit /b 1
    )
    echo       Venv cree.
) else (
    echo       Venv existe deja - OK
)

REM ================================================================
REM  4. DEPENDANCES PIP
REM ================================================================
echo.
echo [4/4] Installation des dependances Python...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip 2>nul
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo       [ERREUR] Installation des dependances echouee.
    echo       Verifie ta connexion internet.
    pause
    exit /b 1
)
echo       Dependances installees - OK

REM ================================================================
REM  TERMINE
REM ================================================================
echo.
echo  ============================================
echo   INSTALLATION TERMINEE !
echo.
echo   Pour lancer Albion GPS :
echo     Clic droit sur run.bat
echo     puis "Executer en tant qu'administrateur"
echo  ============================================
echo.
pause
