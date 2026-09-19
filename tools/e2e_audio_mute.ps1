# End-to-end check for KNOWN_ISSUES #24: "no sound after quitting Beats".
#
# The DAC mute bit lives inside the ES8311 chip, so it survives mpremote
# interrupting the Python VM. That makes the real user sequence testable:
#     reset -> OS boots -> BLE push+run -> BLE stop (same path as long-press OK)
#     -> read REG31 over serial *afterwards*
#
# Reading REG31 must be the LAST step of each phase, because mpremote stops the
# OS. Never reset between the bug trigger and the observation -- a reboot runs
# Audio() again and unmutes the codec by itself, which would fake a pass.
#
# Phases:
#   [1] install the OLD beats (teardown calls mute)  -> expect REG31=0x60
#   [2] same session, BLE-run another app            -> expect REG31=0x00
#   [3] install the FIXED beats                      -> expect REG31=0x00
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\e2e_audio_mute.ps1
#   powershell ... -File tools\e2e_audio_mute.ps1 -Port COM5
param(
    [string]$Port = "COM3",
    [string]$Python = "C:\Users\RanS2\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

$READ = "import passport.config as C`nfrom machine import I2C,Pin`ni=I2C(0,sda=Pin(C.I2C_SDA),scl=Pin(C.I2C_SCL),freq=C.I2C_FREQ)`nprint('REG31=0x%02X' % i.readfrom_mem(C.ADDR_ES8311,0x31,1)[0])"

$script:BleOk = $true

function Reset-OS {
    & $Python -m mpremote connect $Port reset 2>&1 | Out-Null
    Start-Sleep -Seconds 4
}

function Read-Reg31 {
    # Keep the first Audio-free reading: constructing Audio would unmute it.
    $o = & $Python -m mpremote connect $Port exec $READ 2>&1
    $m = ($o | Select-String -Pattern 'REG31=(0x[0-9A-Fa-f]{2})').Matches
    if ($m.Count -gt 0) { return $m[0].Groups[1].Value }
    return "READ-FAIL"
}

function Ble {
    param([string[]]$A)
    $o = & $Python tools\ble_client.py @A 2>&1
    $code = $LASTEXITCODE
    $text = ($o -join "`n")
    $tail = ($o | Where-Object { $_ -match '\S' } | Select-Object -Last 1)
    Write-Host ("      ble {0,-34} [exit {1}] {2}" -f ($A -join ' '), $code, $tail)
    if ($code -ne 0) { $script:BleOk = $false }
    return $text
}

# Build the buggy variant: swap the "deliberately not muting" comment for the
# real mute call the old firmware had.
$src = Get-Content miniapps\beats.py -Raw -Encoding UTF8
$bad = $src -replace '(?s)\r?\n    # Deliberately NOT calling.*?shared codec\.', "`n    ctx.audio.mute(True)"
$badPath = Join-Path $env:TEMP 'beats_old_muted.py'
[System.IO.File]::WriteAllText($badPath, $bad, (New-Object System.Text.UTF8Encoding($false)))
if ($bad -notmatch 'ctx\.audio\.mute\(True\)') { Write-Host "  failed to build old variant"; exit 1 }
Write-Host ("  old variant: {0} bytes" -f (Get-Item $badPath).Length)

$results = @()

Write-Host ""
Write-Host "=== [1] OLD beats (with mute): the exact user sequence ==="
Reset-OS
Ble @('push', $badPath, '--name', 'beats', '--title', 'Beats', '--run') | Out-Null
Start-Sleep -Seconds 2
Ble @('stop') | Out-Null
Start-Sleep -Seconds 1
$r1 = Read-Reg31
Write-Host ("      => REG31=$r1   expect 0x60 (bug: codec left muted after exit)")
$results += , @('1 old beats exit', $r1, '0x60')

Write-Host ""
Write-Host "=== [2] while muted, BLE-run another app IN THE SAME OS SESSION ==="
Write-Host "      (no reset in between: a reboot unmutes by itself and would prove nothing)"
Reset-OS
Ble @('push', $badPath, '--name', 'beats', '--title', 'Beats', '--run') | Out-Null
Start-Sleep -Seconds 2
Ble @('stop') | Out-Null
Start-Sleep -Seconds 1
$runReply = Ble @('run', 'clock')
$runLaunched = ($runReply -match "'ok':\s*True")
Write-Host ("      launch acknowledged by device: {0}  (must be True or this phase is void)" -f $runLaunched)
Start-Sleep -Seconds 1
$r2 = Read-Reg31
Write-Host ("      => REG31=$r2   expect 0x00 (layer-3 reset_state() in launch() cleared it)")
$results += , @('2 next app launch', $r2, '0x00')
if (-not $runLaunched) { $script:BleOk = $false; Write-Host "      [X] launch() never ran -- phase 2 proves nothing" }

Write-Host ""
Write-Host "=== [3] FIXED beats end-to-end ==="
Reset-OS
Ble @('push', 'miniapps\beats.py', '--name', 'beats', '--title', 'Beats', '--run') | Out-Null
Start-Sleep -Seconds 2
Ble @('stop') | Out-Null
Start-Sleep -Seconds 1
$r3 = Read-Reg31
Write-Host ("      => REG31=$r3   expect 0x00 (fix holds)")
$results += , @('3 fixed beats exit', $r3, '0x00')

Write-Host ""
Write-Host ("=" * 62)
$ok = $script:BleOk
foreach ($r in $results) {
    $pass = ($r[1] -eq $r[2])
    if (-not $pass) { $ok = $false }
    Write-Host ("  [{0}] {1,-22} actual {2}  expected {3}" -f $(if ($pass) { "PASS" } else { "FAIL" }), $r[0], $r[1], $r[2])
}
Write-Host ("=" * 62)
Write-Host $(if ($ok) { "  end-to-end: all as expected" } else { "  end-to-end: MISMATCH" })

# Leave the device running PassportOS with the fixed beats installed.
Reset-OS
Remove-Item $badPath -ErrorAction SilentlyContinue
exit $(if ($ok) { 0 } else { 1 })
