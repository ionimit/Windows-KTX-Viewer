#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build KTXViewerSSH.exe – GUI KTX viewer with SSH/SFTP remote file browser.

.DESCRIPTION
    Cleans old build artifacts and rebuilds the executable using PyInstaller.
    Output: dist/KTXViewerSSH.exe

.EXAMPLE
    .\build_ktx_viewer_ssh.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "🔨 Building KTXViewerSSH.exe (GUI + SSH browser)..." -ForegroundColor Cyan

# Clean old builds (preserve other exes in dist)
Write-Host "  Cleaning old artifacts..." -ForegroundColor Gray
Remove-Item -Recurse -Force build, "*.spec" -ErrorAction SilentlyContinue | Out-Null
Remove-Item -Force "dist/KTXViewerSSH.exe" -ErrorAction SilentlyContinue | Out-Null

# Build with PyInstaller
Write-Host "  Running PyInstaller..." -ForegroundColor Gray
pyinstaller `
    --onefile `
    --windowed `
    --name "KTXViewerSSH" `
    --icon NONE `
    --collect-all lzfse `
    --collect-all texture2ddecoder `
    --collect-all tkinterdnd2 `
    --collect-all paramiko `
    --collect-all cryptography `
    --hidden-import paramiko.kex_curve25519 `
    --hidden-import paramiko.kex_ecdh_nist `
    --hidden-import paramiko.kex_gex_sha256 `
    --hidden-import paramiko.kex_group14 `
    --hidden-import paramiko.kex_group16 `
    --hidden-import paramiko.ed25519key `
    ktx_viewer_ssh.py 2>&1 | Select-Object -Last 5

# Report result
if (Test-Path "dist/KTXViewerSSH.exe") {
    $size = [Math]::Round((Get-Item "dist/KTXViewerSSH.exe").Length / 1MB, 1)
    Write-Host "✅ Build successful!" -ForegroundColor Green
    Write-Host "   dist/KTXViewerSSH.exe ($size MB)" -ForegroundColor Green
} else {
    Write-Host "❌ Build failed – executable not found" -ForegroundColor Red
    exit 1
}
