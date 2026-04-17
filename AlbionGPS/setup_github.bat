@echo off
REM ================================================================
REM  Cree le repo GitHub et pousse le code.
REM  Pre-requis : Git installe (https://git-scm.com/download/win)
REM               GitHub CLI installe (https://cli.github.com/)
REM  A lancer UNE SEULE FOIS.
REM ================================================================
cd /d "%~dp0"
title Albion GPS - Setup GitHub

echo.
echo  ============================================
echo   ALBION GPS - Publication sur GitHub
echo  ============================================
echo.

REM --- Verif Git ---
where git >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Git n'est pas installe.
    echo Telecharge-le : https://git-scm.com/download/win
    echo Installe-le avec les options par defaut puis relance ce script.
    pause
    exit /b 1
)

REM --- Verif GitHub CLI ---
where gh >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] GitHub CLI n'est pas installe.
    echo Telecharge-le : https://cli.github.com/
    echo Puis lance "gh auth login" pour te connecter.
    pause
    exit /b 1
)

REM --- Verif connexion GitHub ---
gh auth status >nul 2>&1
if errorlevel 1 (
    echo [INFO] Tu n'es pas connecte a GitHub. Lancement de la connexion...
    gh auth login
)

REM --- Init Git ---
if not exist ".git" (
    echo [Albion GPS] Initialisation du repo Git...
    git init -b main
)

REM --- Commit initial ---
git add .gitignore requirements.txt run.bat README.md
git add src\
git add data\world.xml data\zones.json data\zoneData.json data\zoneData_raw.json
git add tools\
git commit -m "Initial commit - Albion GPS v1.0"

REM --- Creer le repo sur GitHub (public) ---
echo.
echo [Albion GPS] Creation du repo sur GitHub...
gh repo create AlbionGPS --public --source=. --remote=origin --push --description "Albion Online GPS - Navigation temps reel avec detection de zone Photon"

echo.
if errorlevel 1 (
    echo [ERREUR] La creation du repo a echoue.
    echo Si le repo existe deja, lance :
    echo   git remote add origin https://github.com/TON_USERNAME/AlbionGPS.git
    echo   git push -u origin main
) else (
    echo  ============================================
    echo   REPO CREE AVEC SUCCES !
    echo   Tes amis peuvent maintenant telecharger avec :
    echo   git clone https://github.com/TON_USERNAME/AlbionGPS.git
    echo  ============================================
)
echo.
pause
