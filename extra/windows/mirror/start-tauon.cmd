@echo off
rem Starts the Tauon built from this checkout, without the mirror.
rem tauon.exe needs MSYS2's DLLs (SDL3, libpython, libc++), so double-clicking
rem .venv\bin\tauon.exe itself fails with "SDL3.dll was not found".
setlocal
if not defined MSYS2_ROOT set "MSYS2_ROOT=C:\msys64"
set "ROOT=%~dp0..\..\.."
set "EXE=%ROOT%\.venv\bin\tauon.exe"
if not exist "%EXE%" (
	echo Tauon is not built yet. Double-click tauon-mirror.cmd first.
	pause
	exit /b 1
)
if not exist "%MSYS2_ROOT%\clang64\bin\SDL3.dll" (
	echo MSYS2's SDL3.dll is not in %MSYS2_ROOT%\clang64\bin.
	echo Double-click tauon-mirror.cmd: it reinstalls what is missing.
	echo If MSYS2 is somewhere else, set MSYS2_ROOT to that folder first.
	pause
	exit /b 1
)
set "PATH=%MSYS2_ROOT%\clang64\bin;%PATH%"
cd /d "%ROOT%"
"%EXE%" %*
if errorlevel 1 (
	echo Tauon stopped with exit code %ERRORLEVEL%.
	pause
)
