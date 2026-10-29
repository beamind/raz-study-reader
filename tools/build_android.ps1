param(
    [string]$JavaHome,
    [string]$SdkRoot,
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Debug',
    [string]$SigningConfig,
    [switch]$SkipWebBuild,
    [switch]$UseChinaMirror
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$savedEnvironment = @{}
foreach ($name in @('JAVA_HOME', 'ANDROID_HOME', 'GRADLE_USER_HOME', 'PATH',
    'SMART_RAZ_STORE_FILE', 'SMART_RAZ_STORE_PASSWORD', 'SMART_RAZ_KEY_ALIAS', 'SMART_RAZ_KEY_PASSWORD')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    if ($Configuration -eq 'Release') {
        if (!$SigningConfig) { $SigningConfig = Join-Path $env:USERPROFILE '.android\smart-raz-reader\release-signing\signing.json' }
        if (!(Test-Path -LiteralPath $SigningConfig -PathType Leaf)) {
            throw 'Private signing configuration required. See docs/android-release.md.'
        }
        $SigningConfig = (Resolve-Path -LiteralPath $SigningConfig).Path
        $secrets = Get-Content -LiteralPath $SigningConfig -Raw | ConvertFrom-Json
        foreach ($field in @('storeFile', 'storePassword', 'keyAlias', 'keyPassword')) {
            if (!$secrets.$field) { throw "Signing configuration is missing field: $field" }
        }
        $keyPath = $secrets.storeFile
        if (![System.IO.Path]::IsPathRooted($keyPath)) { $keyPath = Join-Path (Split-Path $SigningConfig -Parent) $keyPath }
        if (!(Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Release keystore is missing.' }
        $env:SMART_RAZ_STORE_FILE = (Resolve-Path -LiteralPath $keyPath).Path
        $env:SMART_RAZ_STORE_PASSWORD = $secrets.storePassword
        $env:SMART_RAZ_KEY_ALIAS = $secrets.keyAlias
        $env:SMART_RAZ_KEY_PASSWORD = $secrets.keyPassword
    }
    # Android Studio includes a suitable JDK. Ignore an older system Java if present.
    $javaCandidates = if ($JavaHome) { @($JavaHome) } else {
        @($env:JAVA_HOME,
          "$env:ProgramFiles\Android\Android Studio\jbr",
          "$env:LOCALAPPDATA\Programs\Android Studio\jbr",
          'D:\Program Files\Android\Android Studio\jbr')
    }
    $selectedJava = $null
    foreach ($candidate in $javaCandidates) {
        if (!$candidate -or !(Test-Path -LiteralPath "$candidate\bin\javac.exe")) { continue }
        $version = (& "$candidate\bin\javac.exe" -version 2>&1) -join ' '
        if ($LASTEXITCODE -eq 0 -and $version -match 'javac (\d+)' -and [int]$Matches[1] -ge 21) {
            $selectedJava = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
    if (!$selectedJava) { throw 'JDK 21+ required. Pass -JavaHome or install Android Studio.' }
    if (!$SdkRoot) {
        $SdkRoot = @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, "$env:LOCALAPPDATA\Android\Sdk") |
            Where-Object { $_ -and (Test-Path -LiteralPath "$_\platforms\android-36") } | Select-Object -First 1
    }
    if (!$SdkRoot -or !(Test-Path -LiteralPath "$SdkRoot\platforms\android-36")) {
        throw 'Android SDK platform 36 required. Pass -SdkRoot or install it in Android Studio.'
    }
    $SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path
    $env:JAVA_HOME = $selectedJava
    $env:ANDROID_HOME = $SdkRoot
    if (!$env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME = Join-Path $projectRoot '.local\gradle' }
    $localPythonScripts = Join-Path $projectRoot '.local\reader-pdf\Scripts'
    if (Test-Path -LiteralPath (Join-Path $localPythonScripts 'python.exe')) {
        $env:PATH = "$localPythonScripts;$env:PATH"
        Write-Host "Using project Python: $localPythonScripts\python.exe"
    }
    Write-Host "Using JDK: $selectedJava"
    Write-Host "Using Android SDK: $SdkRoot"
    $localPropertiesPath = Join-Path $projectRoot 'android\local.properties'
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $localPropertiesPath,
        "sdk.dir=$($SdkRoot.Replace('\', '/'))`n",
        $utf8WithoutBom
    )

    Push-Location $projectRoot
    try {
        if (!$SkipWebBuild) {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Reader build failed.' }
        }
        $catalog = Get-Content -LiteralPath 'dist/content/manifest.json' -Raw | ConvertFrom-Json
        if ($catalog.books.Count -gt 0) { throw 'Android builds require the content-free shell. Run npm run build before -SkipWebBuild.' }
        & npx.cmd cap sync android
        if ($LASTEXITCODE -ne 0) { throw 'Capacitor sync failed.' }
        Push-Location android
        try {
            # Incremental APK packaging can leave deleted media as unused ZIP
            # blocks. Clean the app output so removed levels really leave APKs.
            $gradleArguments = @(':app:clean', ":app:assemble$Configuration", '--no-daemon', '--console=plain', '--max-workers=2')
            if ($UseChinaMirror) {
                $gradleArguments += @('--init-script', (Join-Path $PSScriptRoot 'gradle-china-mirror.init.gradle'))
            }
            & .\gradlew.bat @gradleArguments
            if ($LASTEXITCODE -ne 0) { throw 'Android build failed.' }
        } finally { Pop-Location }
        $version = (Get-Content -Raw package.json | ConvertFrom-Json).version
        $variant = $Configuration.ToLowerInvariant()
        $builtApk = "android\app\build\outputs\apk\$variant\app-$variant.apk"
        $toolsPath = Join-Path $SdkRoot 'build-tools\36.1.0'
        & "$toolsPath\apksigner.bat" verify --verbose --print-certs $builtApk
        if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
        & "$toolsPath\zipalign.exe" -c -P 16 4 $builtApk
        if ($LASTEXITCODE -ne 0) { throw 'APK alignment verification failed.' }
        $badging = (& "$toolsPath\aapt.exe" dump badging $builtApk) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'APK manifest inspection failed.' }
        if ($Configuration -eq 'Release' -and $badging -match 'application-debuggable') { throw 'Release APK must not be debuggable.' }
        $output = Join-Path $projectRoot "artifacts\smart-raz-reader-$version-$variant.apk"
        New-Item -ItemType Directory -Force -Path (Split-Path $output -Parent) | Out-Null
        Copy-Item -LiteralPath $builtApk -Destination $output
        Get-Item -LiteralPath $output | Select-Object FullName, Length
        Get-FileHash -LiteralPath $output -Algorithm SHA256
    } finally { Pop-Location }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
