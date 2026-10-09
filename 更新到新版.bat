@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "dist-next\猛人字幕\猛人字幕.exe" (
  echo 没有找到新版（dist-next 文件夹），不需要更新。
  pause
  exit /b 0
)
tasklist /FI "IMAGENAME eq 猛人字幕.exe" 2>nul | find /I "猛人字幕.exe" >nul
if not errorlevel 1 (
  echo 猛人字幕还开着，请先关闭它，再双击这个文件。
  pause
  exit /b 1
)
rem 保留旧版文件夹里的缓存和日志
if exist "dist\猛人字幕\缓存（可随时删除）" move "dist\猛人字幕\缓存（可随时删除）" "dist-next\猛人字幕\" >nul
if exist "dist\猛人字幕\日志（可随时删除）" move "dist\猛人字幕\日志（可随时删除）" "dist-next\猛人字幕\" >nul
if exist "dist" rmdir /s /q "dist"
if exist "dist" (
  echo 旧版删不掉，可能还有文件被占用。请关闭猛人字幕后重试。
  pause
  exit /b 1
)
move "dist-next" "dist" >nul
echo 已更新到新版。桌面上的快捷方式不用改，直接打开就是新版。
pause
