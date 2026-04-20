@echo off
title PENS — Proactive Energy Network System
color 0A

echo ============================================
echo  PENS — Grid OS Starting Up
echo ============================================
echo.

echo [1/5] Checking Docker containers...
docker start pens-db pens-redis >nul 2>&1
echo  Database and Redis started
echo.

echo [2/5] Starting Rust Grid Twin...
start "PENS Grid Twin" cmd /k "cd /d C:\Users\moune\pens-core && cargo run"
timeout /t 5 /nobreak >nul

echo [3/5] Starting AMIL Battery Discovery...
start "PENS AMIL" cmd /k "cd /d C:\Users\moune\pens-core && python python\amil.py"
timeout /t 3 /nobreak >nul

echo [4/5] Starting HQCOE Quantum Optimizer...
start "PENS HQCOE" cmd /k "cd /d C:\Users\moune\pens-core && python python\hqcoe.py"
timeout /t 3 /nobreak >nul

echo [5/5] Starting Approval System...
start "PENS Approval" cmd /k "cd /d %~dp0 && python python\approval.py"
timeout /t 2 /nobreak

echo [6/7] Starting Dead-man Switch...
start "PENS Deadman" cmd /k "cd /d %~dp0 && python python\deadman.py"
timeout /t 2 /nobreak

echo [7/8] Starting Rollback System...
start "PENS Rollback" cmd /k "cd /d %~dp0 && python python\rollback.py"
timeout /t 2 /nobreak

echo [8/9] Starting ML Forecast Engine...
start "PENS Forecast ML" cmd /k "cd /d %~dp0 && python python\forecast_ml.py"
timeout /t 2 /nobreak

echo [9/9] Starting IEC 62351 Command Encryption...
start "PENS IEC62351" cmd /k "cd /d %~dp0 && python python\iec62351.py"
timeout /t 2 /nobreak

echo.
echo ============================================
echo  All systems started
echo  Dashboard: http://127.0.0.1:3001
echo ============================================
echo.
echo Press any key to open dashboard in browser...
pause >nul
start http://127.0.0.1:3001