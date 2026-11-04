@echo off
rem otk without installing the package: otk.cmd gate --dir <dump> ...  (Python 3.11+ in PATH)
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m otk_1c %*
exit /b %ERRORLEVEL%
