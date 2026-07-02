#Requires -RunAsAdministrator
<#
.SYNOPSIS
  One-click EasySynQ appliance install on Windows Server Hyper-V.

.DESCRIPTION
  Creates a Generation-2 Hyper-V VM from the shipped EasySynQ-appliance.vhdx + EasySynQ-seed.iso
  (expects both beside this script), attaches it to an EXTERNAL virtual switch (LAN-reachable),
  boots it, and waits for an IP. First boot self-provisions (10-25 min); the sign-in account and
  one-time bootstrap secret are then on the VM console: log in as 'easysynq' and
  `cat ~/EASYSYNQ-SETUP.txt`.

  Idempotent-ish: refuses to overwrite an existing VM of the same name (remove it explicitly).

.EXAMPLE
  .\Install-EasySynQ.ps1
.EXAMPLE
  .\Install-EasySynQ.ps1 -SwitchName "LAN" -MemoryGB 12 -DiskGB 200
#>
[CmdletBinding()]
param(
  [string]$VmName = "EasySynQ",
  [int]$MemoryGB = 8,
  [int]$CpuCount = 4,
  [int]$DiskGB = 100,
  [string]$SwitchName = "",
  [string]$VmDir = ""
)

$ErrorActionPreference = "Stop"

function Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }

# --- Preconditions -------------------------------------------------------------------------
Step "Checking Hyper-V"
if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
  throw "Hyper-V PowerShell module not found. Enable the Hyper-V role first: Install-WindowsFeature -Name Hyper-V -IncludeManagementTools -Restart"
}
if (Get-VM -Name $VmName -ErrorAction SilentlyContinue) {
  throw "A VM named '$VmName' already exists. Remove it first (Remove-VM '$VmName') or pass -VmName."
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcVhdx = Join-Path $here "EasySynQ-appliance.vhdx"
$seedIso = Join-Path $here "EasySynQ-seed.iso"
foreach ($f in @($srcVhdx, $seedIso)) {
  if (-not (Test-Path $f)) { throw "Missing '$f' — keep the VHDX + seed ISO beside this script." }
}

# --- Network: an EXTERNAL switch so workstations can reach the appliance --------------------
Step "Selecting virtual switch"
if ($SwitchName) {
  $switch = Get-VMSwitch -Name $SwitchName -ErrorAction Stop
} else {
  $switch = Get-VMSwitch -SwitchType External -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $switch) {
    $nics = Get-NetAdapter -Physical | Where-Object Status -eq "Up" | Select-Object -ExpandProperty Name
    throw ("No EXTERNAL virtual switch found (an internal/default switch would hide the appliance " +
      "from your workstations). Create one bound to your LAN adapter, e.g.:`n" +
      "  New-VMSwitch -Name 'LAN' -NetAdapterName '$($nics | Select-Object -First 1)' -AllowManagementOS `$true`n" +
      "then re-run with -SwitchName 'LAN'.")
  }
}
Write-Host "    using switch: $($switch.Name)"

# --- Disk: copy the golden image, grow it (cloud-init expands the FS on first boot) ---------
if (-not $VmDir) { $VmDir = Join-Path (Get-VMHost).VirtualHardDiskPath $VmName }
New-Item -ItemType Directory -Force -Path $VmDir | Out-Null
$vhdx = Join-Path $VmDir "$VmName.vhdx"
if (Test-Path $vhdx) { throw "Disk '$vhdx' already exists — remove it or pick another -VmDir/-VmName." }

Step "Copying appliance disk -> $vhdx"
Copy-Item $srcVhdx $vhdx
Step "Growing disk to ${DiskGB}GB"
Resize-VHD -Path $vhdx -SizeBytes ($DiskGB * 1GB)

$iso = Join-Path $VmDir "EasySynQ-seed.iso"
Copy-Item $seedIso $iso

# --- VM ------------------------------------------------------------------------------------
Step "Creating Generation-2 VM '$VmName' (${MemoryGB}GB RAM, $CpuCount vCPU)"
$vm = New-VM -Name $VmName -Generation 2 -MemoryStartupBytes ($MemoryGB * 1GB) `
  -VHDPath $vhdx -SwitchName $switch.Name
Set-VM -VM $vm -ProcessorCount $CpuCount -AutomaticStartAction Start -AutomaticStopAction ShutDown
# Ubuntu's shim is signed by the Microsoft UEFI CA (NOT the Windows CA) — keep Secure Boot on
# with the right template.
Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate "MicrosoftUEFICertificateAuthority"
Add-VMDvdDrive -VM $vm -Path $iso | Out-Null
Set-VMFirmware -VM $vm -FirstBootDevice (Get-VMHardDiskDrive -VM $vm)

Step "Starting VM"
Start-VM -VM $vm | Out-Null

# --- Wait for an address --------------------------------------------------------------------
Step "Waiting for the VM to report an IPv4 address (up to 5 min)"
$ip = $null
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 5
  $ip = (Get-VMNetworkAdapter -VM $vm).IPAddresses |
    Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1
  if ($ip) { break }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " EasySynQ appliance is booting." -ForegroundColor Green
if ($ip) { Write-Host "   VM address:  $ip   (for reachability checks — the app URL is the hostname)" }
else { Write-Host "   (IP not reported yet — normal until the guest tools start; see Hyper-V Manager -> Networking)" }
Write-Host ""
Write-Host " First boot self-provisions (10-25 min). Then, from a workstation:"
Write-Host "   https://easysynq.local"
Write-Host "   (TLS + sign-in are bound to that hostname; a bare-IP URL will NOT work." -ForegroundColor Yellow
Write-Host "    If mDNS is blocked on your LAN, set a DNS name via easysynq-reconfigure.)" -ForegroundColor Yellow
Write-Host ""
Write-Host " Sign-in account + the one-time setup secret are on the VM:"
Write-Host "   Hyper-V console -> log in 'easysynq' / 'EasySynQ-Setup-1' (forced change)"
Write-Host "   then:  cat ~/EASYSYNQ-SETUP.txt"
Write-Host ""
Write-Host " Progress:  journalctl -fu easysynq-provision   (on the VM console)"
Write-Host "================================================================" -ForegroundColor Green
