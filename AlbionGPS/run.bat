@echo off
REM ================================================================
REM  Albion GPS - LANCEMENT
REM  IMPORTANT : Clic droit -> "Executer en tant qu'administrateur"
REM  Si c'est la premiere fois, lance d'abord install.bat
REM ================================================================
title Albion GPS
cd /d "%~dp0"

echo.
echo  ============================================
echo   ALBION GPS
echo  ============================================
echo.

REM --- Verif admin ---
net session >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Ce script doit etre lance en ADMINISTRATEUR.
    echo.
    echo  Comment faire :
    echo    1. Clic droit sur run.bat
    echo    2. "Executer en tant qu'administrateur"
    echo.
    echo  C'est necessaire pour la detection de zone (sniffing reseau).
    echo.
    pause
    exit /b 1
)

REM --- Verif que l'install a ete faite ---
if not exist ".venv\Scripts\python.exe" (
    echo  [ERREUR] L'installation n'a pas ete faite.
    echo  Lance d'abord install.bat puis reviens ici.
    echo.
    pause
    exit /b 1
)

REM --- Lancement ---
echo  Lancement d'Albion GPS...
echo.
".venv\Scripts\python.exe" -m src.main

echo.
if errorlevel 1 (
    echo  [ERREUR] L'application a quitte avec une erreur.
    echo  Fais une capture d'ecran du texte ci-dessus.
    echo.
)
pause
