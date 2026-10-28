# CPU 构成采样：总 CPU + 各进程 ΔCPU 排行（按采样期消耗的 CPU 秒数）
param([int]$SampleSecs = 15)
if (-not $SampleSecs -or $SampleSecs -lt 3) { $SampleSecs = 15 }

$procs = Get-Process | Where-Object { $_.CPU -gt 0.5 }
$t0 = @{}
foreach ($p in $procs) { $t0[$p.Id] = $p.CPU }
$total0 = (Get-Counter "\Processor(_Total)\% Processor Time").CounterSamples[0].CookedValue
Start-Sleep -Seconds $SampleSecs
$total1 = (Get-Counter "\Processor(_Total)\% Processor Time").CounterSamples[0].CookedValue
Write-Output ("TOTAL CPU = {0:N0}% (sample {1}s)" -f (($total0 + $total1) / 2), $SampleSecs)
Write-Output "---- per-process CPU seconds (top 12) ----"
Get-Process | ForEach-Object {
  if ($t0.ContainsKey($_.Id)) {
    $dt = $_.CPU - $t0[$_.Id]
    if ($dt -gt 0.2) {
      [PSCustomObject]@{ PName = $_.ProcessName; PId = $_.Id;
        CpuSec = [Math]::Round($dt, 1);
        PctOf = [Math]::Round($dt / $SampleSecs * 100, 1);
        WsMb = [Math]::Round($_.WorkingSet64 / 1MB) }
    }
  }
} | Sort-Object CpuSec -Descending | Select-Object -First 12 |
  ForEach-Object { "  {0,-22} PID={1,6}  CPU={2,6:N1}s ({3,5:N1}pct)  WS={4,4:N0}MB" -f $_.PName, $_.PId, $_.CpuSec, $_.PctOf, $_.WsMb }
