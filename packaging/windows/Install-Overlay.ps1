[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Fa-f0-9]{40,64}$')]
    [string]$CertificateThumbprint,
    [string]$TimestampServer
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Use an elevated shell for this installation step only; run Monster normally afterward.'
}
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$source = Join-Path $repository 'artifacts\overlay\MonsterOverlay'
$executable = Join-Path $source 'MonsterOverlay.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Build the renderer first.' }
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint"
if (-not $certificate.HasPrivateKey) { throw 'Selected certificate has no usable private key.' }
$codeSigning = @($certificate.Extensions | Where-Object {
    $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]
} | ForEach-Object { $_.EnhancedKeyUsages } | Where-Object { $_.Value -eq '1.3.6.1.5.5.7.3.3' })
if ($codeSigning.Count -eq 0) {
    throw 'A code-signing certificate is required.'
}
$arguments = @{FilePath = $executable; Certificate = $certificate; HashAlgorithm = 'SHA256'}
if ($TimestampServer) { $arguments.TimestampServer = $TimestampServer }
$signature = Set-AuthenticodeSignature @arguments
if ($signature.Status -ne 'Valid') { throw 'Windows did not validate the signature/certificate chain.' }
$programFiles = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion').ProgramFilesDir
$destination = Join-Path $programFiles 'Monster\Overlay'
if (Test-Path -LiteralPath $destination) { throw 'Destination exists. Review/remove or archive the old install explicitly.' }
New-Item -ItemType Directory -Path $destination | Out-Null
Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse
}
$installed = Get-AuthenticodeSignature -LiteralPath (Join-Path $destination 'MonsterOverlay.exe')
if ($installed.Status -ne 'Valid') { throw 'Installed renderer signature did not validate.' }
Write-Output 'Renderer installed. Close this elevated shell and launch Monster from a normal shell.'
