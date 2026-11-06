# ============================================================================
# FILE: Ensure-Archive-X-LAN.ps1
# PURPOSE: Optional: configures Windows Firewall for LAN access. Needs administrator rights.
# ============================================================================
[CmdletBinding()]
param(
    [ValidateSet('Ensure','Status','Remove')]
    [string]$Action = 'Status',
    [int]$Port = 8443,
    [string]$RuleName = 'Archive X HTTPS',
    [string]$PlayerRuleName = 'Archive X Player Feed',
    [switch]$AllPrivateNetworks
)

$ErrorActionPreference = 'Stop'
$LocalAppData = $env:LOCALAPPDATA; if ([string]::IsNullOrWhiteSpace($LocalAppData)) { $LocalAppData = Join-Path $HOME 'AppData\Local' }
$CliLog = Join-Path $LocalAppData 'Archive X\logs\launcher-cli.log'
$LaunchId = if ($env:ARCHIVE_X_LAUNCH_ID) { $env:ARCHIVE_X_LAUNCH_ID } else { '-' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CliLog) | Out-Null
function Write-LanLog([string]$Message) { Add-Content -LiteralPath $CliLog -Value ("{0} [lan] launch_id=$LaunchId {1}" -f ([DateTime]::UtcNow.ToString('o')), $Message) -Encoding UTF8 }
Write-LanLog "phase=lan_firewall action=$Action port=$Port"
if ($Action -in @('Ensure','Remove')) {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error 'Administrator privileges are required for firewall changes.'
        Write-LanLog 'status=error reason=administrator_required'
        exit 1
    }
}
$profiles = 'Private'
if ($AllPrivateNetworks) { $profiles = 'Private,Domain' }

function Get-ArchiveRule([string]$Name) {
    Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
}

function Ensure-TcpRule([string]$Name, [string]$LocalPort, [string]$Description) {
    $rule = Get-ArchiveRule $Name
    if ($rule) {
        $portFilters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue)
        $portOk = $false
        foreach ($filter in $portFilters) {
            if ([string]$filter.Protocol -eq 'TCP' -and [string]$filter.LocalPort -eq $LocalPort) { $portOk = $true }
        }
        if (-not $portOk) {
            $rule | Remove-NetFirewallRule -ErrorAction Stop
            $rule = $null
        } else {
            Set-NetFirewallRule -DisplayName $Name -Enabled True -Direction Inbound -Action Allow -Profile $profiles -RemoteAddress LocalSubnet -ErrorAction Stop
        }
    }
    if (-not $rule) {
        New-NetFirewallRule -DisplayName $Name -Direction Inbound -Protocol TCP -LocalPort $LocalPort -Action Allow -Profile $profiles -RemoteAddress LocalSubnet -Description $Description -ErrorAction Stop | Out-Null
    }
}

switch ($Action) {
    'Status' {
        $https = Get-ArchiveRule $RuleName
        $player = Get-ArchiveRule $PlayerRuleName
        if (-not $https -and -not $player) {
            Write-LanLog 'status=not_configured'
            Write-Host "Archive X LAN firewall rules are not configured."
            exit 1
        }
        @($https,$player) | Where-Object { $_ } | Select-Object DisplayName, Enabled, Direction, Action, Profile | Format-List
        Write-LanLog 'status=success action=status'
        exit 0
    }
    'Remove' {
        foreach ($name in @($RuleName,$PlayerRuleName)) {
            $rule = Get-ArchiveRule $name
            if ($rule) { $rule | Remove-NetFirewallRule -ErrorAction Stop; Write-Host "Removed $name." }
        }
        Write-LanLog 'status=success action=remove'
        exit 0
    }
    'Ensure' {
        Ensure-TcpRule $RuleName ([string]$Port) 'Archive X local/LAN HTTPS access'
        # Player feed uses a bounded local range so external-player sessions can
        # allocate a free port without opening arbitrary inbound TCP ports.
        Ensure-TcpRule $PlayerRuleName '8444-8464' 'Archive X local/LAN player feed'
        Write-LanLog "status=success action=ensure profiles=$profiles https_port=$Port player_ports=8444-8464"
        Write-Host "Archive X LAN firewall rules are configured for TCP $Port and player range -LocalPort 8444-8464 on $profiles networks, limited to LocalSubnet."
        exit 0
    }
}
