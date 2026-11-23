@echo off
setlocal EnableDelayedExpansion
rem Track editor: atmosphere (fog, sky, weather along the road) and AI lines.
rem   run_track_editor.bat                     -> asks which track to open
rem   run_track_editor.bat hometown            -> opens that track
rem   run_track_editor.bat alps --atmosphere path\to\file.ini
rem   run_track_editor.bat hometown --ai       -> AI line mode (same as run_ai_line.bat)
rem The game folder comes from nfs5tools.ini ([paths] game) or NFS5_GAME.
rem Loading a track takes ~10 s before the window opens.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set GAME=
for /f "delims=" %%G in ('python tools\track\nfs5cfg.py --get GAME') do set "GAME=%%G"
if not exist "!GAME!\GameData\Track" (
    echo Porsche Unleashed folder not found: "!GAME!"
    echo Copy nfs5tools.example.ini to nfs5tools.ini and set [paths] game.
    pause
    exit /b 1
)

set TRACK=%~1
if not "%TRACK%"=="" goto run

echo === Track editor: choose a track ===
echo.
set N=0
for %%f in ("!GAME!\GameData\Track\*.crp") do (
    set "NAME=%%~nf"
    if "!NAME:.=!"=="!NAME!" (
        if exist "!GAME!\GameData\Track\!NAME!.fsh" (
            set /a N+=1
            set "TRACK_!N!=!NAME!"
            set "NUM=  !N!"
            echo  !NUM:~-3!  !NAME!
        )
    )
)
echo.
set CHOICE=
set /p CHOICE=Number or track name (Enter = alps):
if "!CHOICE!"=="" set CHOICE=alps
set TRACK=!CHOICE!
for /l %%i in (1,1,!N!) do if "!CHOICE!"=="%%i" set TRACK=!TRACK_%%i!
if not exist "!GAME!\GameData\Track\!TRACK!.crp" (
    echo No such track: !TRACK!
    pause
    exit /b 1
)
set EXTRA=
goto launch

:run
set EXTRA=%2 %3 %4 %5

:launch
python tools\track_editor "!GAME!\GameData\Track" !TRACK! !EXTRA! %EDITOR_EXTRA%
if errorlevel 1 (
    echo.
    echo Editor exited with an error. See the message above.
    pause
)
