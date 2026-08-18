@echo off
setlocal
cd /d %~dp0

if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  py -m venv .venv
  if errorlevel 1 goto :error

  echo Instalando dependencias...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :error

  echo Instalando Chromium de Playwright...
  .venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 goto :error
)

echo.
echo Walmart SC Toreo - extraccion completa
echo Se abrira un navegador. Completa manualmente cualquier verificacion de Walmart,
echo confirma SC Toreo / CP 11220 y despues regresa a la consola para continuar.
echo.

.venv\Scripts\python.exe scripts\walmart_run_full.py --profile-dir .walmart_profile
if errorlevel 1 goto :error

echo.
echo Archivos generados:
echo   output\walmart_cuidado_bucal_sc_toreo.xlsx
echo   output\walmart_cuidado_de_la_ropa_sc_toreo.xlsx
echo.
pause
exit /b 0

:error
echo.
echo La ejecucion termino con error. Revisa diagnostics\ y los mensajes anteriores.
pause
exit /b 1
