# M0-T1 (c): Windows runner feasibility. Run on windows-latest (m0-spike.yml) or the reference PC.
$ErrorActionPreference = "Continue"
$r = @{}
$base = Join-Path $env:TEMP "m0t1c"; New-Item -ItemType Directory -Force $base | Out-Null
New-Item -ItemType Directory -Force "$base\target" | Out-Null
try { cmd /c mklink /J "$base\junction" "$base\target" | Out-Null; $r.junction = Test-Path "$base\junction" } catch { $r.junction = $false }
try { subst Q: $base; $r.subst = Test-Path "Q:\"; subst Q: /D } catch { $r.subst = $false }
try {
  $f = "$base\placeholder.jpg"; Set-Content $f "x"
  $attr = [System.IO.File]::GetAttributes($f)
  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000 cannot be set via SetAttributes; record whether the API rejects it
  try { [System.IO.File]::SetAttributes($f, [System.IO.FileAttributes]([int]$attr -bor 0x400000)); $r.recall_attr = "set" } catch { $r.recall_attr = "rejected: $($_.Exception.Message)" }
} catch { $r.recall_attr = "error" }
$out = @{ id = "M0-T1c"; status = "measured"; machine = @{ os = "Windows"; label = $env:OPAGENT_M0_MACHINE }; metrics = $r; pass = ($r.junction -and $r.subst); notes = "junction/subst/recall attribute feasibility" }
$out | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $PSScriptRoot "..\results\M0-T1c.json") -Encoding utf8
$out | ConvertTo-Json -Depth 4
