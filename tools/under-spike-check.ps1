# Runs the proof app (native/under-spike) and checks, on the real desktop, what the screen shows. The proof page is built like the app's full-screen player: a solid page, hidden
# around a see-through top-layer box (html.ax-under), so a point of the page's own content showing over the video would fail the video point:
#   a point inside the video only            -> the video's colour (green)
#   a point under the page's opaque box      -> the page's colour (magenta) when the page is over the video ("under"); the video's when it is not ("popup")
#   a point under the page's half-white box  -> the video blended with white (the page really blends over the picture)
# Usage: powershell -File tools/under-spike-check.ps1 -Exe <under-spike.exe> -Video <clip> -AxDll <axmpv.dll> -MpvDir <folder> -Layer under|popup -Name <label> [-Frameless]
param([string]$Exe, [string]$Video, [string]$AxDll, [string]$MpvDir, [string]$Layer = "under", [string]$Name = "run", [switch]$Frameless, [string]$Out = "under-shots")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$status = Join-Path $Out "$Name-status.json"
Remove-Item $status -ErrorAction SilentlyContinue
$env:SPIKE_AXMPV_DLL = $AxDll; $env:SPIKE_MPV_DIR = $MpvDir; $env:SPIKE_VIDEO = $Video; $env:SPIKE_STATUS = (Resolve-Path $Out).Path + "\$Name-status.json"; $env:SPIKE_LAYER = $Layer
if ($Frameless) { $env:SPIKE_DECORATIONS = "0" } else { Remove-Item Env:SPIKE_DECORATIONS -ErrorAction SilentlyContinue }
$app = Start-Process -FilePath $Exe -PassThru
try {
  $doc = $null
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $status) { try { $d = Get-Content $status -Raw | ConvertFrom-Json } catch { continue }
      if ($d.ok -eq $false) { Write-Host "the proof app failed: $($d.error_text)"; exit 2 }
      if ($d.ok -and $d.position -gt 2.5 -and $d.window_visible) { $doc = $d; break } }
  }
  if (-not $doc) { Write-Host "the video never played: $(Get-Content $status -Raw -ErrorAction SilentlyContinue)"; exit 2 }
  Start-Sleep -Seconds 2
  $doc = Get-Content $status -Raw | ConvertFrom-Json
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  $png = Join-Path $Out "$Name.png"; $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
  $s = $doc.client_w / 900.0
  function At($cssX, $cssY) { $x = [int]($doc.client_x + $cssX * $s); $y = [int]($doc.client_y + $cssY * $s); if ($x -ge $bmp.Width -or $y -ge $bmp.Height) { return $null }; $p = $bmp.GetPixel($x, $y); return @($p.R, $p.G, $p.B) }
  $video = At 600 400; $opaque = At 140 100; $half = At 400 100
  Write-Host "screen $($b.Width)x$($b.Height); client at $($doc.client_x),$($doc.client_y) size $($doc.client_w)x$($doc.client_h); layer $($doc.layer); window $($doc.window_width)x$($doc.window_height)"
  Write-Host "video point   : $video"; Write-Host "opaque box    : $opaque"; Write-Host "half-white box: $half"
  Write-Host "diagnostics:`n$($doc.diagnostics)"
  # Plain comparisons on three integers, no helper functions: the helpers answered "false" for a correct 0 200 0 on the runner. A failure prints the numbers it judged.
  function Rgb($c) { if ($null -eq $c) { return @(-1, -1, -1) }; return @([int]$c[0], [int]$c[1], [int]$c[2]) }
  $v = Rgb $video; $o = Rgb $opaque; $h = Rgb $half
  $videoGreen = ($v[1] -ge 150) -and ($v[0] -le 80) -and ($v[2] -le 80)
  $boxMagenta = ($o[0] -ge 200) -and ($o[2] -ge 200) -and ($o[1] -le 60)
  $halfBlend = ($h[0] -ge 90) -and ($h[2] -ge 90) -and ($h[1] -ge 180) -and ($h[0] -le 200)
  Write-Host "judged: video=$($v -join ',') green=$videoGreen; box=$($o -join ',') magenta=$boxMagenta; half=$($h -join ',') blend=$halfBlend"
  $fail = @()
  if (-not $videoGreen) { $fail += "the video point is not the video's green: $($v -join ' ')" }
  if ($Layer -eq "under") {
    if (-not $boxMagenta) { $fail += "the page's opaque box does not show over the video: $($o -join ' ')" }
    if (-not $halfBlend) { $fail += "the page's half-white box is not blended over the video: $($h -join ' ')" }
  } elseif ($boxMagenta) { $fail += "control run: the page's box shows over the video, so this check cannot tell the layers apart" }
  if ($fail.Count) { $fail | ForEach-Object { Write-Host "FAIL  $_" }; exit 1 }
  Write-Host "PASS  $Name"
} finally {
  Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
}
