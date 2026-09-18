# Run with: powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
# Leave parameters unbound so CLI options pass through without PowerShell parsing.
$ErrorActionPreference = 'Stop'
$launchArguments = @($args)
$repoDir = $PSScriptRoot
$uvVersion = '0.11.14'
$uvDir = Join-Path $repoDir ".bootstrap\uv\$uvVersion"
$uvBin = Join-Path $uvDir 'uv.exe'
$offline = $launchArguments -contains '--offline'

if (($launchArguments -contains '--help') -or ($launchArguments -contains '-h')) {
    Write-Output 'Usage: .\start.ps1 [--data-dir PATH] [--config PATH] [--port PORT]'
    Write-Output '                  [--no-browser] [--skip-core] [--setup-only] [--offline]'
    Write-Output 'Installs the runtime, prepares free source search, and opens the app.'
    Write-Output 'OpenAI configuration is optional in browser Settings. No paid work runs.'
    exit 0
}

try {
    Write-Output 'NYC Housing Research'
    Write-Output '[1/4] Preparing the application runtime...'
    if (-not (Test-Path -LiteralPath $uvBin)) {
        $existingUv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
        $useExisting = $false
        if ($existingUv) {
            $versionOutput = & $existingUv.Source --version
            $useExisting = ($LASTEXITCODE -eq 0) -and ($versionOutput -match "^uv $([regex]::Escape($uvVersion))( |$)")
        }
        if ($useExisting) {
            $uvBin = $existingUv.Source
        } else {
            if ($offline) { throw 'The runtime is missing. Run once with internet access.' }
            Write-Output "Downloading uv $uvVersion from astral.sh into .bootstrap (no administrator access)."
            $installer = Join-Path ([IO.Path]::GetTempPath()) ("nyc-housing-installer-" + [guid]::NewGuid() + '.ps1')
            $oldInstallDir = $env:UV_UNMANAGED_INSTALL
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri "https://astral.sh/uv/$uvVersion/install.ps1" -OutFile $installer
                $env:UV_UNMANAGED_INSTALL = $uvDir
                # A child process contains the upstream installer's exit/environment changes.
                $shellExecutable = if (Test-Path (Join-Path $PSHOME 'pwsh.exe')) { 'pwsh.exe' } else { 'powershell.exe' }
                & (Join-Path $PSHOME $shellExecutable) -NoProfile -ExecutionPolicy Bypass -File $installer
                if ($LASTEXITCODE -ne 0) { throw 'The runtime installer could not finish.' }
            } finally {
                $env:UV_UNMANAGED_INSTALL = $oldInstallDir
                if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer }
            }
            if (-not (Test-Path -LiteralPath $uvBin)) { throw 'The runtime installer did not produce the expected executable.' }
        }
    }

    $env:UV_PROJECT_ENVIRONMENT = Join-Path $repoDir '.venv'
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    $env:PYTHONUNBUFFERED = '1'
    if ($offline) { $env:UV_OFFLINE = '1' }
    Write-Output '[2/4] Preparing Python 3.12 and locked dependencies (including secure key storage)...'
    & $uvBin sync --project $repoDir --python 3.12 --locked --extra credentials --inexact
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check the error above; no source installation was started.' }
    & $uvBin run --project $repoDir --no-sync --no-env-file nyc-housing start @launchArguments
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Error.WriteLine('See docs/Troubleshooting.md, then rerun this command.')
    exit 3
}
