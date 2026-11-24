# בנייה עם הכלים המקומיים שב-.tools/ (בלי התקנה במערכת ובלי לשנות PATH קבוע).
# שימוש:  powershell -ExecutionPolicy Bypass -File android\build_local.ps1 testDebugUnitTest assembleDebug
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path (Split-Path -Parent $here) ".tools"
$jdk = Get-ChildItem $tools -Directory -Filter "jdk-17*" | Select-Object -First 1
if (-not $jdk) { throw "JDK 17 not found in $tools" }
$env:JAVA_HOME = $jdk.FullName
$env:ANDROID_HOME = Join-Path $tools "android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:GRADLE_USER_HOME = Join-Path $tools "gradle-home"        # מטמון Gradle נשאר בתוך .tools
$env:ANDROID_USER_HOME = Join-Path $tools "android-user-home" # debug.keystore נשאר בתוך .tools
$env:Path = "$($env:JAVA_HOME)\bin;$($env:Path)"
if (-not (Test-Path (Join-Path $here "local.properties"))) {
    $sdk = $env:ANDROID_HOME.Replace("\", "/").Replace(":", "\:")
    Set-Content -Encoding ascii -Path (Join-Path $here "local.properties") -Value "sdk.dir=$sdk"
}
Push-Location $here
try { & .\gradlew.bat --no-daemon @args; exit $LASTEXITCODE } finally { Pop-Location }
