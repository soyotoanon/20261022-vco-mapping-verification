@echo off
setlocal
set "VIVADO_BIN=D:\Xilinx\Vivado\2023.2\bin"
set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%..\build" mkdir "%SCRIPT_DIR%..\build"
call "%VIVADO_BIN%\vivado.bat" -mode batch -notrace -source "%SCRIPT_DIR%build.tcl" -log "%SCRIPT_DIR%..\build\revision_build.log" -journal "%SCRIPT_DIR%..\build\revision_build.jou"
exit /b %ERRORLEVEL%
