@echo off
rem Starts the Tauon built from this checkout, without the mirror.
rem tauon.exe needs MSYS2's DLLs (SDL3, libpython, libc++), so double-clicking
rem .venv\bin\tauon.exe itself fails with "SDL3.dll was not found".
setlocal
rem MSYS2 can be on any drive: use MSYS2_ROOT if set, else look on C: to Z:
if not defined MSYS2_ROOT if exist "%SystemDrive%\msys64\usr\bin\bash.exe" set "MSYS2_ROOT=%SystemDrive%\msys64"
if not defined MSYS2_ROOT for %%D in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do (
	if not defined MSYS2_ROOT for %%F in (msys64 msys2 tools\msys64 tools\msys2) do (
		if not defined MSYS2_ROOT if exist "%%D:\%%F\usr\bin\bash.exe" set "MSYS2_ROOT=%%D:\%%F"
	)
)
if not defined MSYS2_ROOT (
	echo MSYS2 was not found. Install it from https://www.msys2.org/ ^(any drive works^),
	echo or set MSYS2_ROOT to its folder, e.g. set MSYS2_ROOT=D:\msys64
	pause
	exit /b 1
)
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
	echo If you have another MSYS2 elsewhere, set MSYS2_ROOT to that folder first.
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
