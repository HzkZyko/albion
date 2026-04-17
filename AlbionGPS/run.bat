@echo off
REM Lance Albion GPS sur Windows. Cree un venv a la premiere execution
REM et installe automatiquement les dependances depuis requirements.txt.
setlocal
cd /d "%~dp0"

REM --- Detection d'une installation Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python n'est pas installe ou pas dans le PATH.
    echo Telecharge-le depuis https://www.python.org/downloads/ et coche
    echo "Add python.exe to PATH" pendant l'installation.
    pause
    exit /b 1
)

REM --- Creation du venv si necessaire ---
if not exist ".venv\Scripts\python.exe" (
    echo [Albion GPS] Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERREUR] Impossible de creer le venv.
        pause
        exit /b 1
    )
)

REM --- Installation / mise a jour des dependances ---
REM On reinstalle silencieusement a chaque lancement : pip est idempotent
REM et detecte tout de suite si requirements.txt a change.
echo [Albion GPS] Verification des dependances...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [ERREUR] Installation des dependances echouee.
    pause
    exit /b 1
)

REM --- Demande d'elevation admin (necessaire pour le sniffing Photon) ---
REM On relance en admin si on ne l'est pas deja. Le sniffing reseau via
REM Npcap/scapy exige des droits administrateur sous Windows.
net session >nul 2>&1
if errorlevel 1 (
    echo [Albion GPS] Elevation en administrateur...
    powershell -Command "Start-Process -FilePath '%~dpnx0' -Verb RunAs"
    exit /b 0
)

REM --- Lancement de l'app ---
".venv\Scripts\python.exe" -m src.main
pause
endlocal
