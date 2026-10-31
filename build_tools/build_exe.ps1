# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把 StutterTriage 打包成单文件 EXE（PyInstaller）。

.DESCRIPTION
    · 依赖构建环境（含 PyInstaller 的 venv）默认放在项目目录**外**：
        <项目>\..\_build_env\stuttertriage_venv
      可用 -Venv 覆盖，或设环境变量 STUTTERTRIAGE_BUILD_VENV。
    · 产物：<项目>\dist\StutterTriage.exe（单文件，双击即用）
    · PyInstaller 的临时工作目录也在项目外（_build_env\work），
      打包完项目里只多出 dist\StutterTriage.exe，不留一堆 build 垃圾。

.EXAMPLE
    pwsh -File build_tools\build_exe.ps1
    pwsh -File build_tools\build_exe.ps1 -RebuildVenv     # 重建构建环境
#>
[CmdletBinding()]
param(
    [string]$Venv = "",
    [switch]$RebuildVenv,
    [switch]$KeepWork
)

$ErrorActionPreference = 'Stop'

$Proj = Split-Path -Parent $PSScriptRoot
$Spec = Join-Path $PSScriptRoot 'StutterTriage.spec'
$Dist = Join-Path $Proj 'dist'
$WorkRoot = Join-Path (Split-Path -Parent $Proj) '_build_env'
$Work = Join-Path $WorkRoot 'work'

if (-not $Venv) {
    $Venv = if ($env:STUTTERTRIAGE_BUILD_VENV) { $env:STUTTERTRIAGE_BUILD_VENV }
            else { Join-Path $WorkRoot 'stuttertriage_venv' }
}

Write-Host "== StutterTriage 打包 ==" -ForegroundColor Cyan
Write-Host "  项目      : $Proj"
Write-Host "  构建环境  : $Venv"
Write-Host "  产物      : $Dist\StutterTriage.exe"

if ($RebuildVenv -and (Test-Path $Venv)) {
    Write-Host "  -> 删除旧构建环境" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $Venv)) {
    Write-Host "  -> 创建构建环境（--system-site-packages，复用系统已装的 PyQt6/pyqtgraph/numpy）"
    python -m venv $Venv --system-site-packages
}
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "构建环境缺 python：$Py" }

# 确认 PyInstaller 在
& $Py -m PyInstaller --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  -> 安装 PyInstaller" -ForegroundColor Yellow
    & $Py -m pip install --quiet --upgrade pyinstaller
}
$piVer = (& $Py -m PyInstaller --version).Trim()
Write-Host "  PyInstaller: $piVer"

# 用 UTF-8 输出，避免中文路径/文案在控制台乱码
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force $Dist, $Work | Out-Null

$t0 = Get-Date
& $Py -m PyInstaller --noconfirm --clean `
    --distpath $Dist `
    --workpath $Work `
    $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（exit=$LASTEXITCODE）" }
$elapsed = [int]((Get-Date) - $t0).TotalSeconds

$exe = Join-Path $Dist 'StutterTriage.exe'
if (-not (Test-Path $exe)) { throw "没有生成 $exe" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

if (-not $KeepWork) {
    # PyInstaller 的中间产物（几十 MB）用完即删，项目外也不留垃圾
    Remove-Item -Recurse -Force (Join-Path $Work 'StutterTriage') -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "  完成：$exe" -ForegroundColor Green
Write-Host "  体积：$mb MB    耗时：${elapsed}s"
Write-Host ""
Write-Host "  提示：exe 是便携式的，配置/日志位置——" -ForegroundColor DarkGray
Write-Host "    · stuttertriage_config.json 会生成在 exe 同目录（把旧的拷过去即可沿用设置）" -ForegroundColor DarkGray
Write-Host "    · 运行日志在 %USERPROFILE%\.stuttertriage\stuttertriage_diag.log" -ForegroundColor DarkGray
Write-Host "    · 需要完整采集（ETW）时：右键 exe → 以管理员身份运行" -ForegroundColor DarkGray
