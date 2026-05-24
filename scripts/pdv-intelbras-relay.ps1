$ErrorActionPreference = 'Stop'

$destIp = '192.168.24.227'
$destPort = 38801
$listenPorts = @(52101, 52102, 52103, 52104, 52105, 52107, 52108, 52109, 52110, 52111, 52112)
$logDir = 'C:\PDVIntelbrasRelay'
$logFile = Join-Path $logDir 'relay.log'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-RelayLog {
    param([string]$Message)
    $line = "[{0}] {1}`r`n" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    [System.IO.File]::AppendAllText($logFile, $line, [System.Text.Encoding]::UTF8)
}

$clients = @()
foreach ($port in $listenPorts) {
    $udp = New-Object System.Net.Sockets.UdpClient($port)
    $clients += [pscustomobject]@{ Port = $port; Udp = $udp }
    Write-RelayLog "porta $port escutando e repassando para $destIp`:$destPort"
}

Write-RelayLog 'relay iniciado'

while ($true) {
    foreach ($client in $clients) {
        try {
            while ($client.Udp.Available -gt 0) {
                $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
                $bytes = $client.Udp.Receive([ref]$remote)
                [void]$client.Udp.Send($bytes, $bytes.Length, $destIp, $destPort)

                $text = [System.Text.Encoding]::UTF8.GetString($bytes)
                if ($text.Length -gt 180) { $text = $text.Substring(0, 180) }
                $text = $text.Replace("`r", " ").Replace("`n", " ")
                Write-RelayLog ("porta {0}: {1}:{2} -> iMHDX | {3}" -f $client.Port, $remote.Address, $remote.Port, $text)
            }
        } catch {
            Write-RelayLog ("porta {0}: erro {1}" -f $client.Port, $_.Exception.Message)
            Start-Sleep -Milliseconds 500
        }
    }
    Start-Sleep -Milliseconds 50
}
