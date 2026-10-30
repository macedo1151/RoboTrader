@echo off
REM Inicia o RoboTrader em modo SIMULADO (nenhuma ordem e enviada).
REM Requisitos: MetaTrader 5 da Genial aberto e logado. Deixe esta janela aberta.
REM Para parar: feche a janela ou pressione Ctrl+C.
cd /d "%~dp0"
echo.
echo === Decisao do dia ===
.venv-mt5\Scripts\python -m robotrader signal
echo.
echo === Robo em execucao (simulado) ===
.venv-mt5\Scripts\python -m robotrader live
pause
