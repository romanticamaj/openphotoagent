# M0-T1 (c): Windows runner feasibility. Run on windows-latest (m0-spike.yml) or the reference PC.
$ErrorActionPreference = "Continue"
$r = @{}
$base = Join-Path $env:TEMP "m0t1c"; New-Item -ItemType Directory -Force $base | Out-Null
New-Item -ItemType Directory -Force "$base\target" | Out-Null
try { cmd /c mklink /J "$base\junction" "$base\target" | Out-Null; $r.junction = Test-Path "$base\junction" } catch { $r.junction = $false }
try { subst Q: $base; $r.subst = Test-Path "Q:\"; subst Q: /D } catch { $r.subst = $false }
try {
  $f = "$base\placeholder.jpg"; Set-Content $f "x"
  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000. System.IO.FileAttributes has no such member, so call the Win32 API
  # directly; a .NET cast failure says nothing about what the OS allows.
  Add-Type -Namespace M0 -Name K32 -MemberDefinition '[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool SetFileAttributesW(string p, uint a); [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern uint GetFileAttributesW(string p);'
  $before = [M0.K32]::GetFileAttributesW($f)
  $ok = [M0.K32]::SetFileAttributesW($f, [uint32]($before -bor 0x400000))
  $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
  $after = [M0.K32]::GetFileAttributesW($f)
  $r.recall_attr = "SetFileAttributesW ok=$ok lastError=$err before=0x$('{0:X}' -f $before) after=0x$('{0:X}' -f $after) stuck=$((($after -band 0x400000) -ne 0))"
} catch { $r.recall_attr = "error" }
$out = @{ id = "M0-T1c"; status = "measured"; machine = @{ os = "Windows"; label = $env:OPAGENT_M0_MACHINE }; metrics = $r; pass = ($r.junction -and $r.subst); notes = "junction/subst/recall attribute feasibility" }
$out | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $PSScriptRoot "..\results\M0-T1c.json") -Encoding utf8
$out | ConvertTo-Json -Depth 4
