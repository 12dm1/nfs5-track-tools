@echo off
rem Texture seam and tone editor for AI-regenerated textures.
rem   run_texture_editor.bat                       -> empty window, drop files in
rem   run_texture_editor.bat new.png               -> opens that texture
rem   run_texture_editor.bat new.png original.png  -> and the original to match
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python tools\texture_editor %*
if errorlevel 1 (
    echo.
    echo Editor exited with an error. See the message above.
    pause
)
