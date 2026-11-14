# Freeze the backend into packaging\backend\dist\archive-x-backend\ (Windows).
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$py = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
& $py -m pip install -q pyinstaller -r requirements.txt
& $py -m PyInstaller --noconfirm --clean --distpath packaging\backend\dist --workpath packaging\backend\build packaging\backend\archive_x_backend.spec
$size = '{0:N0} MB' -f ((Get-ChildItem packaging\backend\dist\archive-x-backend -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "built: packaging\backend\dist\archive-x-backend ($size)"
