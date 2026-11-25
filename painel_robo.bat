@echo off
REM Abre o painel para ligar e desligar o robo (http://localhost:8765). Deixe esta janela aberta.
cd /d "%~dp0"
.venv\Scripts\python -m robotrader.painel
pause
