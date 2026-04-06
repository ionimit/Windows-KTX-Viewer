#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build KTXViewer.exe – standalone Windows GUI KTX viewer with drag-and-drop support.

.DESCRIPTION
    Cleans old build artifacts and rebuilds the executable using PyInstaller.
    Output: dist/KTXViewer.exe

.EXAMPLE
    .\build_ktx_viewer.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "🔨 Building KTXViewer.exe (windowed GUI)..." -ForegroundColor Cyan

# Clean old builds (preserve other exes in dist)
Write-Host "  Cleaning old artifacts..." -ForegroundColor Gray
Remove-Item -Recurse -Force build, "*.spec" -ErrorAction SilentlyContinue | Out-Null
Remove-Item -Force "dist/KTXViewer.exe" -ErrorAction SilentlyContinue | Out-Null

# Build with PyInstaller
Write-Host "  Running PyInstaller..." -ForegroundColor Gray
pyinstaller `
    --onefile `
    --windowed `
    --name "KTXViewer" `
    --icon NONE `
    --collect-all lzfse `
    --collect-all texture2ddecoder `
    --collect-all tkinterdnd2 `
    ktx_viewer.py 2>&1 | Select-Object -Last 5

# Report result
if (Test-Path "dist/KTXViewer.exe") {
    $size = [Math]::Round((Get-Item "dist/KTXViewer.exe").Length / 1MB, 1)
    Write-Host "✅ Build successful!" -ForegroundColor Green
    Write-Host "   dist/KTXViewer.exe ($size MB)" -ForegroundColor Green
} else {
    Write-Host "❌ Build failed – executable not found" -ForegroundColor Red
    exit 1
}
