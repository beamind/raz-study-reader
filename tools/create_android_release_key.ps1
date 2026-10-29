param(
    [Parameter(Mandatory)][string]$PrivateDirectory,
    [Parameter(Mandatory)][string]$JavaHome
)
$ErrorActionPreference = 'Stop'
$target = [System.IO.Path]::GetFullPath($PrivateDirectory)
$project = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
if ($target.StartsWith($project + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or $target -eq $project) {
    throw 'Keep release signing keys outside the repository.'
}
if (Test-Path -LiteralPath $target) { throw 'Signing directory already exists. Existing keys are never overwritten.' }
$keytool = Join-Path $JavaHome 'bin\keytool.exe'
if (!(Test-Path -LiteralPath $keytool)) { throw 'keytool.exe is missing.' }
New-Item -ItemType Directory -Path $target | Out-Null
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
foreach ($sid in @($currentSid, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $target -AclObject $acl
$password = [Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$savedPassword = $env:SMART_RAZ_KEYGEN_PASSWORD
try {
    $env:SMART_RAZ_KEYGEN_PASSWORD = $password
    & $keytool -genkeypair -noprompt -keystore (Join-Path $target 'release.p12') -storetype PKCS12 `
        -storepass:env SMART_RAZ_KEYGEN_PASSWORD -keypass:env SMART_RAZ_KEYGEN_PASSWORD `
        -alias smart-raz-reader -keyalg RSA -keysize 3072 -sigalg SHA256withRSA -validity 10000 `
        -dname 'CN=Smart RAZ Reader, O=Smart RAZ Reader, C=CN'
    if ($LASTEXITCODE -ne 0) { throw 'Release key generation failed.' }
    $config = @{ storeFile = 'release.p12'; storePassword = $password; keyAlias = 'smart-raz-reader'; keyPassword = $password }
    [System.IO.File]::WriteAllText((Join-Path $target 'signing.json'), ($config | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    & $keytool -exportcert -rfc -keystore (Join-Path $target 'release.p12') -storepass:env SMART_RAZ_KEYGEN_PASSWORD `
        -alias smart-raz-reader -file (Join-Path $target 'release-certificate.pem')
    if ($LASTEXITCODE -ne 0) { throw 'Certificate export failed.' }
    Write-Host "Private release key created at: $target"
    Write-Host 'Keep this entire private directory backed up. Do not upload it with the APK or commit it.'
} finally { $env:SMART_RAZ_KEYGEN_PASSWORD = $savedPassword }
