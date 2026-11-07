[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
Push-Location $repository
try {
    # Keep the user's application environment untouched; frozen helper uses Python 3.12.
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $repository '.overlay-build-venv'
    $entry = Join-Path $repository 'packaging\windows\overlay_main.py'
    $manifest = Join-Path $repository 'packaging\windows\overlay.manifest'
    $sourcePath = Join-Path $repository 'src'
    $outputPath = Join-Path $repository 'artifacts\overlay'
    $workPath = Join-Path $repository 'artifacts\overlay-work'
    & uv run --frozen --python 3.12 --with pyinstaller==6.16.0 python -m PyInstaller `
        --noconfirm --clean --onedir --windowed --name MonsterOverlay --paths $sourcePath `
        --manifest $manifest `
        --distpath $outputPath --workpath $workPath `
        --specpath $workPath $entry
    if ($LASTEXITCODE -ne 0) { throw 'Overlay build failed.' }
    Write-Output 'Unsigned build prepared. Signing and trusted installation are still required.'
}
finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    Pop-Location
}
