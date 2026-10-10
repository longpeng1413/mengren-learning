@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "dist-next\猛人学习\猛人学习.exe" (
  echo 没有找到新版（dist-next 文件夹），不需要更新。
  pause
  exit /b 0
)
for %%P in (猛人学习.exe 猛人字幕.exe) do (
  tasklist /FI "IMAGENAME eq %%P" 2>nul | find /I "%%P" >nul
  if not errorlevel 1 (
    echo 猛人学习还开着，请先关闭它，再双击这个文件。
    pause
    exit /b 1
  )
)
rem 保留旧版文件夹里的缓存和日志（1.6 以前的文件夹叫「猛人字幕」）
for %%D in (猛人学习 猛人字幕) do (
  if exist "dist\%%D\缓存（可随时删除）" if not exist "dist-next\猛人学习\缓存（可随时删除）" move "dist\%%D\缓存（可随时删除）" "dist-next\猛人学习\" >nul
  if exist "dist\%%D\日志（可随时删除）" if not exist "dist-next\猛人学习\日志（可随时删除）" move "dist\%%D\日志（可随时删除）" "dist-next\猛人学习\" >nul
)
if exist "dist" rmdir /s /q "dist"
if exist "dist" (
  echo 旧版删不掉，可能还有文件被占用。请关闭猛人学习后重试。
  pause
  exit /b 1
)
move "dist-next" "dist" >nul
echo 已更新到新版：dist\猛人学习\猛人学习.exe
echo 桌面快捷方式如果还指向旧的「猛人字幕.exe」，请删掉它，重新给 猛人学习.exe 建一个。
pause
