@echo off
REM ================================================================
REM  Albion GPS - Lanceur automatique
REM  Installe Python + Npcap + dependances si necessaire.
REM  Compatible avec un PC vierge.
REM ================================================================
title Albion GPS
cd /d "%~dp0"

REM --- ETAPE 0 : Elevation admin en PREMIER (avant tout le reste) ---
net session >nul 2>&1
if errorlevel 1 (
    echo [Albion GPS] Demande d'elevation administrateur...
    echo Ce script va se relancer en mode administrateur.
    echo.
    REM Ecrit un mini VBS pour s'elever (methode la plus fiable sur Windows)
    echo Set UAC = CreateObject^("Shell.Application"^) > "%TEMP%\albion_elevate.vbs"
    echo UAC.ShellExecute "%~dpnx0", "", "%~dp0", "runas", 1 >> "%TEMP%\albion_elevate.vbs"
    wscript "%TEMP%\albion_elevate.vbs"
    exit /b 0
)

REM A partir d'ici on est ADMIN.
cd /d "%~dp0"

echo.
echo  ============================================
echo   ALBION GPS - Installation et lancement
echo  ============================================
echo.

REM ================================================================
REM  1. VERIFIER SI PYTHON EST INSTALLE
REM ================================================================
where python >nul 2>&1
if errorlevel 1 (
    echo [Albion GPS] Python non detecte. Installation automatique...
    echo.

    set "PY_URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    set "PY_INSTALLER=%TEMP%\python_installer.exe"

    echo [Albion GPS] Telechargement de Python 3.12...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%PY_URL%', '%PY_INSTALLER%')"
    if not exist "%PY_INSTALLER%" (
        echo [ERREUR] Echec du telechargement de Python.
        echo Telecharge-le manuellement : https://www.python.org/downloads/
        echo IMPORTANT : coche "Add python.exe to PATH" pendant l'installation.
        pause
        exit /b 1
    )

    echo [Albion GPS] Installation de Python (1-2 min)...
    "%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_launcher=1
    if errorlevel 1 (
        echo [ERREUR] L'installation de Python a echoue.
        echo Lance "%PY_INSTALLER%" manuellement et coche "Add to PATH".
        pause
        exit /b 1
    )

    REM Rafraichir le PATH pour cette session
    for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "PATH=%%B"
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "PATH=%%B;%PATH%"

    echo [Albion GPS] Python installe avec succes.
    echo.

    where python >nul 2>&1
    if errorlevel 1 (
        echo [ATTENTION] Python installe mais pas encore dans le PATH.
        echo Ferme cette fenetre, REDEMARRE ton PC, puis relance run.bat.
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [Albion GPS] %%v detecte

REM ================================================================
REM  2. VERIFIER SI NPCAP EST INSTALLE
REM ================================================================
if not exist "%SystemRoot%\System32\Npcap\wpcap.dll" (
    echo.
    echo [Albion GPS] Npcap non detecte. Necessaire pour la detection de zone.
    echo [Albion GPS] Telechargement de Npcap...

    set "NPCAP_URL=https://npcap.com/dist/npcap-1.80.exe"
    set "NPCAP_INSTALLER=%TEMP%\npcap_installer.exe"

    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%NPCAP_URL%', '%NPCAP_INSTALLER%')"
    if not exist "%NPCAP_INSTALLER%" (
        echo [ERREUR] Echec du telechargement de Npcap.
        echo Telecharge-le manuellement : https://npcap.com/#download
        pause
        exit /b 1
    )

    echo [Albion GPS] Lancement de l'installeur Npcap...
    echo   IMPORTANT : Coche "Install Npcap in WinPcap API-compatible Mode"
    echo   puis clique Install.
    echo.
    "%NPCAP_INSTALLER%"

    if not exist "%SystemRoot%\System32\Npcap\wpcap.dll" (
        echo.
        echo [ATTENTION] Npcap pas detecte apres install.
        echo La detection de zone ne marchera pas sans Npcap.
        pause
    ) else (
        echo [Albion GPS] Npcap installe.
    )
    echo.
)

REM ================================================================
REM  3. CREER LE VENV SI NECESSAIRE
REM ================================================================
if not exist ".venv\Scripts\python.exe" (
    echo [Albion GPS] Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERREUR] Impossible de creer le venv.
        pause
        exit /b 1
    )
    echo [Albion GPS] Venv cree.
)

REM ================================================================
REM  4. INSTALLER LES DEPENDANCES
REM ================================================================
echo [Albion GPS] Verification des dependances...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip 2>nul
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERREUR] Installation des dependances echouee.
    echo Verifie ta connexion internet et relance.
    pause
    exit /b 1
)
echo [Albion GPS] Dependances OK.
echo.

REM ================================================================
REM  5. LANCEMENT
REM ================================================================
echo  ============================================
echo   Lancement d'Albion GPS...
echo  ============================================
echo.
".venv\Scripts\python.exe" -m src.main

echo.
if errorlevel 1 (
    echo [ERREUR] L'application a quitte avec une erreur.
    echo Fais une capture d'ecran du texte ci-dessus et envoie-la.
)
echo Appuie sur une touche pour fermer...
pause >nul
