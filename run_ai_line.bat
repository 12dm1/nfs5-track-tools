@echo off
rem AI line view of a track: racing lines, walls, .aispeed zones and race_test traces
rem (speed, target speed, pedals, events). See tools\track_editor\ai_ui.py.
rem   run_ai_line.bat            -> asks which track to open
rem   run_ai_line.bat hometown   -> opens that track directly
set EDITOR_EXTRA=--ai
call "%~dp0run_track_editor.bat" %*
