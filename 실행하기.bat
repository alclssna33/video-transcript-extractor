@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================
echo   영상 대본 추출기
echo ========================================
echo.

set "PY="

REM Prefer the official py launcher - avoids the Microsoft Store "python.exe" stub
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PY=py -3"
    )
)

REM Fall back to "python" only if it is a real interpreter, not the Store stub
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PY=python"
        )
    )
)

if not defined PY (
    echo [오류] Python을 찾을 수 없습니다.
    echo.
    echo   1^) https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치하세요.
    echo   2^) 설치 화면에서 "Add python.exe to PATH"를 꼭 체크하세요.
    echo   3^) 설치가 끝나면 이 파일을 다시 실행하세요.
    echo.
    echo   이미 Python을 설치했는데도 이 메시지가 보인다면, 설정 -^> 앱 -^> 고급 앱 설정
    echo   -^> 앱 실행 별칭 에서 "python.exe"로 시작하는 항목을 꺼주세요.
    echo.
    pause
    exit /b 1
)

REM Check ffmpeg is installed - warn only, keep going
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [경고] ffmpeg가 설치되어 있지 않습니다. 영상 처리에 반드시 필요합니다.
    echo         https://www.gyan.dev/ffmpeg/builds/ 에서 release essentials를 내려받아
    echo         압축을 풀고, bin 폴더를 PATH에 추가해주세요.
    echo.
)

REM First run - create venv and install packages
if not exist ".venv\Scripts\python.exe" (
    echo 처음 실행이라 필요한 프로그램을 설치합니다. 몇 분 걸릴 수 있어요...
    echo.
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )

    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인해주세요.
        pause
        exit /b 1
    )
    echo.
    echo 설치가 끝났습니다.
    echo.
)

REM Create .env from example if missing - credentials are entered via settings screen
if not exist ".env" (
    copy ".env.example" ".env" >nul
)

echo 잠시 후 브라우저가 자동으로 열립니다: http://localhost:8000
echo 프로그램을 끄려면 이 검은 창을 닫으면 됩니다.
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8000"

".venv\Scripts\python.exe" -m uvicorn app.main:create_app --factory --port 8000

pause
