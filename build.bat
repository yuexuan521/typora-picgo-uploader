@echo off
REM 打包单文件 exe 到 dist\typora-picgo-uploader.exe
setlocal

cd /d "%~dp0"

echo [1/3] 安装 PyInstaller...
python -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 goto :failed

echo [2/3] 运行测试...
python -m unittest discover -s tests
if errorlevel 1 goto :failed

echo [3/3] 打包...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name typora-picgo-uploader ^
  --distpath dist ^
  --workpath build ^
  --specpath build ^
  main.py
if errorlevel 1 goto :failed

echo.
echo 完成： dist\typora-picgo-uploader.exe
exit /b 0

:failed
echo.
echo 打包失败。
exit /b 1
