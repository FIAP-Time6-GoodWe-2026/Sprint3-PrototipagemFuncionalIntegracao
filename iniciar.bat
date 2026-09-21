@echo off
REM ============================================================================
REM  ChargeGrid Intelligence - NexusCharge
REM  Sprint 3 | FIAP + GoodWe EV Challenge 2026
REM
REM  Ponto de entrada do aplicativo. Basta dar dois cliques neste arquivo.
REM ============================================================================

REM Pagina de codigo UTF-8: sem isto o banner e os acentos saem como lixo no
REM console do Windows, que por padrao usa cp1252.
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"

title ChargeGrid Intelligence - NexusCharge

REM Trabalha sempre na pasta deste .bat, nao na pasta de onde ele foi chamado.
cd /d "%~dp0"

echo.
echo  ChargeGrid Intelligence - NexusCharge
echo  Sprint 3 ^| FIAP + GoodWe EV Challenge 2026
echo  ----------------------------------------------------------

REM ---------------------------------------------------------------------------
REM  1. Localiza o Python
REM     O launcher "py" e o caminho padrao no Windows; "python" e o reserva.
REM ---------------------------------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo.
    echo  [ERRO] Python nao encontrado.
    echo.
    echo  Instale a versao 3.11 ou superior em:
    echo      https://www.python.org/downloads/
    echo.
    echo  IMPORTANTE: marque "Add python.exe to PATH" na primeira tela
    echo  do instalador, senao o Windows nao acha o Python depois.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do set "VERSAO=%%v"
echo  Python   : %VERSAO%

REM ---------------------------------------------------------------------------
REM  2. Garante as dependencias
REM     Sao apenas duas: flask e pytest. O banco usa o sqlite3, que ja vem
REM     junto com o Python - nao ha nada a instalar por causa dele.
REM     Os dois sao verificados: com flask presente e pytest faltando, a tela
REM     de testes quebrava e o .bat dizia que estava tudo certo.
REM ---------------------------------------------------------------------------
set "FALTA="
%PY% -c "import flask" >nul 2>&1 || set "FALTA=1"
%PY% -c "import pytest" >nul 2>&1 || set "FALTA=1"

if defined FALTA (
    echo  Pacotes  : instalando flask e pytest...
    %PY% -m pip install --quiet --disable-pip-version-check flask pytest
    if errorlevel 1 (
        echo.
        echo  [ERRO] Nao foi possivel instalar as dependencias.
        echo  Verifique a conexao com a internet e tente de novo, ou rode
        echo  manualmente:  pip install flask pytest
        echo.
        pause
        exit /b 1
    )
    echo  Pacotes  : instalados
) else (
    echo  Pacotes  : ok
)

REM ---------------------------------------------------------------------------
REM  3. Menu
REM ---------------------------------------------------------------------------
:MENU
echo  ----------------------------------------------------------
echo.
echo   [1]  Aplicativo web        mapa, recarga, carteira e pagamento
echo   [2]  Gestao de sessoes     menu de terminal (Estruturas de Dados)
echo   [3]  Rodar os testes       suite completa no terminal
echo   [4]  Sair
echo.
set "OPCAO="
set /p "OPCAO=  Escolha [1]: "
if not defined OPCAO set "OPCAO=1"

if "%OPCAO%"=="1" goto WEB
if "%OPCAO%"=="2" goto MENU_DSA
if "%OPCAO%"=="3" goto TESTES
if "%OPCAO%"=="4" exit /b 0

echo.
echo  Opcao invalida: "%OPCAO%". Escolha de 1 a 4.
echo.
goto MENU

REM ---------------------------------------------------------------------------
:WEB
REM  Abre o navegador alguns segundos depois, ja com o servidor de pe.
REM  Roda em paralelo para nao travar a subida do Flask.
REM ---------------------------------------------------------------------------
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:5001'" >nul 2>&1

echo.
echo  Navegador: abrindo em http://localhost:5001
echo  Para parar o servidor: Ctrl+C
echo  ----------------------------------------------------------
echo.

REM O proprio app.py imprime as contas de demonstracao no banner.
%PY% app.py
goto FIM

REM ---------------------------------------------------------------------------
:MENU_DSA
REM  Menu de terminal da entrega de Estruturas de Dados. Roda sobre o mesmo
REM  sistema do aplicativo web: mesma classe de sessao, mesma lista, mesmos
REM  algoritmos de busca e ordenacao. Nao precisa do Flask.
REM ---------------------------------------------------------------------------
echo.
echo  ----------------------------------------------------------
%PY% menu.py
goto FIM

REM ---------------------------------------------------------------------------
:TESTES
REM ---------------------------------------------------------------------------
echo.
echo  ----------------------------------------------------------
%PY% -m pytest test_chargegrid.py -q
goto FIM

REM ---------------------------------------------------------------------------
:FIM
REM  A janela fica aberta para a mensagem poder ser lida.
REM ---------------------------------------------------------------------------
echo.
echo  ----------------------------------------------------------
if errorlevel 1 (
    echo  Encerrado com erro. A mensagem esta acima.
) else (
    echo  Encerrado.
)
echo.
pause
