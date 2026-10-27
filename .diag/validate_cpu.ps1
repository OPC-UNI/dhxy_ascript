# Validate: locate task child process and sample CPU% (with phase from log tail)
param([int]$Seconds = 15)

Write-Output "==== task process ===="
$task = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match "multiprocessing.spawn" } |
  Select-Object -First 1
if (-not $task) {
  Write-Output "no task child process found (task not running?)"
  exit 1
}
Write-Output ("task PID={0} created {1}" -f $task.ProcessId, $task.CreationDate)

Write-Output "==== log tail (phase) ===="
$log = Get-ChildItem "C:\Users\Wang\Desktop\ascript\dhxy2\logs" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log.FullName -Tail 4

Write-Output "==== CPU% sample (${Seconds}s) ===="
$c0 = (Get-Process -Id $task.ProcessId).CPU
Start-Sleep -Seconds $Seconds
$p1 = Get-Process -Id $task.ProcessId -ErrorAction SilentlyContinue
if (-not $p1) { Write-Output "process exited during sample"; exit 1 }
$dt = $p1.CPU - $c0
Write-Output ("CPU% = {0:N0}%  (avg {1:N2} cores)" -f ($dt/$Seconds*100), ($dt/$Seconds))
