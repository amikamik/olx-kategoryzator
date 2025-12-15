@echo off
title Kategoryzator OLX - Automatyczna kategoryzacja produktow
color 0A
echo.
echo ================================================================================
echo           KATEGORYZATOR OLX - Automatyczna Kategoryzacja Produktow
echo ================================================================================
echo.
cd /d "%~dp0"
python 08_kategoryzator_ekspert.py
echo.
echo ================================================================================
echo                         Nacisnij dowolny klawisz...
echo ================================================================================
pause >nul
