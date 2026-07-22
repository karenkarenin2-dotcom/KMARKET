# Создаёт ярлык KMARKET на рабочем столе.
# Запускать ПОСЛЕ любого переименования папки проекта — ярлык хранит
# абсолютный путь и после переезда указывает в пустоту.
#
#     powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root 'KMARKET.bat'
if (-not (Test-Path $target)) { throw "Не найден $target" }

$linkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'KMARKET.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $target
$link.WorkingDirectory = $root
$link.Description = 'KMARKET — аналитика рынка WoW (KareninTeam)'
$link.Save()

Write-Output "Ярлык создан: $linkPath"
Write-Output "Указывает на: $target"
