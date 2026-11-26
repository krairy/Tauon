<#
.SYNOPSIS
Starts Tauon with its remote control on, then makes it follow what your phone
plays through Jellyfin (tauon_mirror.py).

.DESCRIPTION
1. Picks Tauon: built from this source checkout (needs MSYS2), else an
   installed Tauon. Builds or updates the source build when needed.
2. Turns on "Enable remote control" in tauon.conf (only while Tauon is closed,
   because Tauon rewrites that file when it exits), starts Tauon and waits
   until http://127.0.0.1:7814/api1/version answers.
3. Runs the mirror until you press Ctrl+C or close the window.

Windows PowerShell 5.1 compatible. See README.md next to this file.

.EXAMPLE
.\tauon-mirror.ps1 -Device moto
.EXAMPLE
.\tauon-mirror.ps1 -Device moto -InstallStartup
#>
[CmdletBinding()]
param(
	# Follow only this Jellyfin device (part of its name, e.g. "moto" or "Finamp")
	[string]$Device = $env:TAUON_MIRROR_DEVICE,
	# Seconds Tauon may drift before it is re-synced (0 = the mirror's default, 2.5)
	[double]$Tolerance = 0,
	# Jellyfin server, e.g. http://my-server:8096 (default: the server set up in Tauon)
	[string]$JellyfinUrl = $env:JELLYFIN_URL,
	# Jellyfin API key (default: the login set up in Tauon)
	[string]$JellyfinApiKey = $env:JELLYFIN_API_KEY,
	# Leave Tauon's volume alone
	[switch]$NoMute,
	# Use this Tauon program instead of looking for one
	[string]$TauonExe = $env:TAUON_EXE,
	# Use this tauon.conf instead of the one that belongs to the chosen Tauon
	[string]$TauonConf = $env:TAUON_CONF,
	# Prefer an installed Tauon over building this source checkout
	[switch]$UseInstalled,
	# Don't build or update the source build, just use what is there
	[switch]$SkipBuild,
	# Rebuild the source build's dependencies and native code
	[switch]$Rebuild,
	# MSYS2 folder (default: C:\msys64)
	[string]$Msys2Root = $env:MSYS2_ROOT,
	# Python that runs the mirror (default: found automatically)
	[string]$Python = $env:TAUON_MIRROR_PYTHON,
	# Seconds to wait for Tauon's remote control to answer
	[int]$TauonTimeout = 120,
	# Also run all this at login (shortcut in the Startup folder)
	[switch]$InstallStartup,
	# Remove the login shortcut and stop
	[switch]$RemoveStartup,
	# Never ask questions (used by the login shortcut)
	[switch]$NoPrompt
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ApiUrl = 'http://127.0.0.1:7814/api1/version'
$MirrorScript = Join-Path $PSScriptRoot 'tauon_mirror.py'
$StateDir = Join-Path $(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }) 'tauon-mirror'
$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Tauon Mirror.lnk'
$TauonProcessNames = @('tauon', 'Tauon Music Box')  # source build, installed build

# --- Messages ---------------------------------------------------------------

$ProblemMarker = 'TAUON-MIRROR: '

function Say([string]$Text) { Write-Host "  $Text" }
function Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Warn([string]$Text) { Write-Host "  NOTE: $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { throw ($ProblemMarker + $Text) }

function Test-CanAsk {
	return (-not $NoPrompt) -and [Environment]::UserInteractive -and ($Host.Name -eq 'ConsoleHost')
}

function Ask-YesNo([string]$Question, [bool]$Default = $true) {
	if (-not (Test-CanAsk)) { return $Default }
	$hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
	$answer = Read-Host "  $Question $hint"
	if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
	return $answer.Trim().ToLower().StartsWith('y')
}

# --- Command lines ----------------------------------------------------------

function ConvertTo-Argument([string]$Value) {
	# Quoting as CommandLineToArgvW and the C runtime read it
	if ($Value -ne '' -and $Value -notmatch '[\s"]') { return $Value }
	$escaped = [regex]::Replace($Value, '(\\*)"', { param($m) $m.Groups[1].Value * 2 + '\"' })
	$escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
	return '"' + $escaped + '"'
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory = $PSScriptRoot) {
	# Runs in this console (output goes straight to it) and returns the exit code
	$info = New-Object System.Diagnostics.ProcessStartInfo
	$info.FileName = $Exe
	$info.Arguments = ($Arguments | ForEach-Object { ConvertTo-Argument ([string]$_) }) -join ' '
	$info.UseShellExecute = $false
	$info.WorkingDirectory = $WorkingDirectory
	$process = [System.Diagnostics.Process]::Start($info)
	try {
		while (-not $process.WaitForExit(500)) { }
	} finally {
		# Ctrl+C reaches the child too; give it time to finish cleanly before this script ends
		if (-not $process.HasExited) { [void]$process.WaitForExit(10000) }
	}
	return $process.ExitCode
}

# --- Finding things ---------------------------------------------------------

function Get-SourceCheckout {
	$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
	if ((Test-Path -LiteralPath (Join-Path $root 'pyproject.toml')) -and
		(Test-Path -LiteralPath (Join-Path $root 'src\tauon\__main__.py'))) {
		return $root
	}
	return $null
}

function Find-Msys2 {
	$candidates = @($Msys2Root, 'C:\msys64', "$env:SystemDrive\msys64", 'C:\tools\msys64',
		"$env:USERPROFILE\scoop\apps\msys2\current")
	foreach ($dir in $candidates) {
		if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'usr\bin\bash.exe'))) { return $dir }
	}
	return $null
}

function Find-InstalledTauon {
	$found = @()
	$keys = @(
		'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
		'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
		'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')
	foreach ($key in $keys) {
		$entries = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue |
			Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -like 'Tauon*' }
		foreach ($entry in $entries) {
			if ($entry.PSObject.Properties['InstallLocation'] -and $entry.InstallLocation) {
				$found += Join-Path $entry.InstallLocation 'Tauon Music Box.exe'
			}
		}
	}
	$found += Join-Path $env:ProgramFiles 'Tauon Music Box\Tauon Music Box.exe'
	if (${env:ProgramFiles(x86)}) { $found += Join-Path ${env:ProgramFiles(x86)} 'Tauon Music Box\Tauon Music Box.exe' }
	if ($env:LOCALAPPDATA) { $found += Join-Path $env:LOCALAPPDATA 'Programs\Tauon Music Box\Tauon Music Box.exe' }
	foreach ($exe in $found) {
		if (Test-Path -LiteralPath $exe) { return $exe }
	}
	return $null
}

function Get-TauonUserDir([string]$Exe) {
	# Same order as user_data_directory() in src/tauon_native/main.cpp
	if ($env:TAUON_USER_DATA_DIR) { return $env:TAUON_USER_DATA_DIR }
	$dir = Split-Path -Parent $Exe
	if ((Test-Path -LiteralPath (Join-Path $dir 'portable')) -or
		(Test-Path -LiteralPath (Join-Path $dir '_internal\portable'))) {
		return Join-Path $dir 'user-data'
	}
	if ($env:LOCALAPPDATA) { return Join-Path $env:LOCALAPPDATA 'TauonMusicBox' }
	return Join-Path $env:APPDATA 'TauonMusicBox'
}

function Get-TauonProcess {
	# Callers wrap this in @(): .Count on a single object fails under StrictMode in PowerShell 5.1
	Get-Process -Name $TauonProcessNames -ErrorAction SilentlyContinue
}

function Test-TauonApi {
	try {
		$request = [System.Net.WebRequest]::Create($ApiUrl)
		$request.Proxy = $null
		$request.Timeout = 2000
		$response = $request.GetResponse()
		try {
			$reader = New-Object System.IO.StreamReader($response.GetResponseStream())
			return $reader.ReadToEnd() -match 'version'
		} finally {
			$response.Close()
		}
	} catch {
		return $false
	}
}

function Wait-TauonApi([int]$Seconds, [bool]$WatchProcess) {
	$deadline = (Get-Date).AddSeconds($Seconds)
	$sawProcess = $false
	while ((Get-Date) -lt $deadline) {
		if (Test-TauonApi) { Write-Host ''; return $true }
		$running = @(Get-TauonProcess).Count -gt 0
		if ($running) { $sawProcess = $true }
		if ($WatchProcess -and $sawProcess -and -not $running) { Write-Host ''; return $false }
		Write-Host '.' -NoNewline
		Start-Sleep -Milliseconds 1000
	}
	Write-Host ''
	return $false
}

# --- tauon.conf -------------------------------------------------------------

$RemotePattern = '(?m)^([ \t]*enable-remote-interface[ \t]*=[ \t]*)([A-Za-z]+)'

function Get-RemoteSetting([string]$Conf) {
	# $true / $false, or $null when tauon.conf or the key is missing
	if (-not (Test-Path -LiteralPath $Conf)) { return $null }
	$match = [regex]::Match([IO.File]::ReadAllText($Conf), $RemotePattern)
	if (-not $match.Success) { return $null }
	return $match.Groups[2].Value -eq 'true'
}

function Enable-RemoteSetting([string]$Conf) {
	# Tauon takes the first "enable-remote-interface = ..." line in any section
	$utf8 = New-Object System.Text.UTF8Encoding($false)
	if (Test-Path -LiteralPath $Conf) {
		$text = [IO.File]::ReadAllText($Conf)
		$regex = New-Object System.Text.RegularExpressions.Regex($RemotePattern)
		if ($regex.IsMatch($text)) {
			$text = $regex.Replace($text, { param($m) $m.Groups[1].Value + 'true' }, 1)
		} else {
			$text = $text.TrimEnd() + "`r`n`r`n[app]`r`nenable-remote-interface = true`r`n"
		}
	} else {
		New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Conf) | Out-Null
		$text = "[app]`r`nenable-remote-interface = true`r`n"
	}
	[IO.File]::WriteAllText($Conf, $text, $utf8)
}

# --- Steps ------------------------------------------------------------------

function Initialize-Submodules([string]$Repo) {
	if ((Test-Path -LiteralPath (Join-Path $Repo 'src\phazor\miniaudio\miniaudio.h')) -and
		(Test-Path -LiteralPath (Join-Path $Repo 'src\phazor\kissfft\kiss_fft.c'))) {
		return
	}
	$git = Get-Command git -ErrorAction SilentlyContinue
	if (-not $git) {
		Fail ("Parts of the source code (src\phazor\miniaudio and src\phazor\kissfft) are missing. " +
			"Install Git from https://git-scm.com/download/win, then run this again.")
	}
	Say 'Downloading the missing parts of the source code (git submodules).'
	$code = Invoke-Native $git.Source @('-C', $Repo, 'submodule', 'update', '--init', '--recursive')
	if ($code -ne 0) { Fail "Downloading the git submodules failed. Run in the Tauon folder: git submodule update --init --recursive" }
}

function Build-FromSource([string]$Repo, [string]$Msys) {
	Step 'Building Tauon from this folder (skipped steps are already done)'
	Initialize-Submodules $Repo
	$bash = Join-Path $Msys 'usr\bin\bash.exe'
	$arguments = @('-l', 'extra/windows/mirror/build-tauon.sh')
	if ($Rebuild) { $arguments += '--force' }
	# A CLANG64 login shell that stays in the working directory
	$env:MSYSTEM = 'CLANG64'
	$env:CHERE_INVOKING = '1'
	try {
		$code = Invoke-Native $bash $arguments $Repo
		# MSYS2 ends its own shell after updating its core packages: run once more
		$stage = Join-Path $Repo 'build\windows-mirror\pacman.stage'
		if ($code -ne 0 -and (Test-Path -LiteralPath $stage)) {
			Remove-Item -LiteralPath $stage
			Say 'MSYS2 updated itself; continuing.'
			$code = Invoke-Native $bash $arguments $Repo
		}
	} finally {
		Remove-Item Env:MSYSTEM, Env:CHERE_INVOKING -ErrorAction SilentlyContinue
	}
	return $code -eq 0
}

function Select-Tauon {
	# Returns @{ Exe; Source; Msys }
	if ($TauonExe) {
		if (-not (Test-Path -LiteralPath $TauonExe)) { Fail "Tauon was not found at $TauonExe (from -TauonExe or TAUON_EXE)." }
		return @{ Exe = (Resolve-Path -LiteralPath $TauonExe).Path; Source = $false; Msys = (Find-Msys2) }
	}
	$repo = Get-SourceCheckout
	$installed = Find-InstalledTauon
	$msys = Find-Msys2
	if ($UseInstalled -and -not $installed) { Warn 'No installed Tauon was found (-UseInstalled).' }
	if ($repo -and -not ($UseInstalled -and $installed)) {
		if ($msys) {
			Say "Using Tauon built from $repo"
			if ($installed) { Say '(add -UseInstalled to use the installed Tauon instead)' }
			return @{ Exe = (Join-Path $repo '.venv\bin\tauon.exe'); Source = $true; Repo = $repo; Msys = $msys }
		}
		if ($installed) {
			Warn 'MSYS2 is not installed, so the Tauon in this folder cannot be built.'
			if (Ask-YesNo "Use the installed Tauon ($installed) instead?") {
				return @{ Exe = $installed; Source = $false; Msys = $null }
			}
		}
		Fail ("To build Tauon from this folder you need MSYS2. Install it from https://www.msys2.org/ " +
			"(keep the suggested folder C:\msys64), then run this again. " +
			"Or install the regular Tauon (https://tauonmusicbox.rocks/, or: winget install Taiko2k.TauonMusicBox) and use -UseInstalled.")
	}
	if ($installed) {
		Say "Using the installed Tauon: $installed"
		return @{ Exe = $installed; Source = $false; Msys = $msys }
	}
	Fail ("Tauon was not found. Install it from https://tauonmusicbox.rocks/ (or run: winget install Taiko2k.TauonMusicBox), " +
		"or point to it with -TauonExe 'C:\path\to\Tauon Music Box.exe'.")
}

function Add-MsysToPath([string]$Msys) {
	# The source build's tauon.exe and MSYS2's Python load their DLLs from there.
	# Not for an installed Tauon, which must load its own bundled copies.
	if ($Msys) {
		$bin = Join-Path $Msys 'clang64\bin'
		if (($env:PATH -split ';') -notcontains $bin) { $env:PATH = "$bin;$env:PATH" }
	}
}

function Get-MissingMsysPackages($Tauon) {
	# MSYS2 packages whose DLLs the source build's tauon.exe needs before it can show anything
	$bin = Join-Path $Tauon.Msys 'clang64\bin'
	$needed = [ordered]@{ 'libc++.dll' = 'libc++'; 'SDL3.dll' = 'sdl3'; 'SDL3_image.dll' = 'sdl3-image'; 'SDL3_ttf.dll' = 'sdl3-ttf' }
	$cfg = Join-Path $Tauon.Repo '.venv\pyvenv.cfg'
	if (Test-Path -LiteralPath $cfg) {
		$match = Select-String -LiteralPath $cfg -Pattern '^\s*version\s*=\s*(\d+)\.(\d+)' | Select-Object -First 1
		if ($match) { $needed["libpython$($match.Matches[0].Groups[1].Value).$($match.Matches[0].Groups[2].Value).dll"] = 'python' }
	}
	return @($needed.Keys | Where-Object { -not (Test-Path -LiteralPath (Join-Path $bin $_)) } | ForEach-Object { $needed[$_] })
}

function Repair-MsysDlls($Tauon) {
	$missing = @(Get-MissingMsysPackages $Tauon)
	if ($missing.Count -eq 0) { return }
	Step 'Reinstalling MSYS2 files Tauon needs to start'
	Warn ("Missing from $(Join-Path $Tauon.Msys 'clang64\bin'): " + ($missing -join ', '))
	$packages = ($missing | ForEach-Object { "mingw-w64-clang-x86_64-$_" }) -join ' '
	$env:MSYSTEM = 'CLANG64'
	$env:CHERE_INVOKING = '1'
	try {
		[void](Invoke-Native (Join-Path $Tauon.Msys 'usr\bin\bash.exe') @('-lc', "pacman -S --noconfirm --ask=4 $packages") $Tauon.Repo)
	} finally {
		Remove-Item Env:MSYSTEM, Env:CHERE_INVOKING -ErrorAction SilentlyContinue
	}
	# A newer Python leaves .venv without its DLL; the build script remakes .venv then
	if (-not $SkipBuild) { [void](Build-FromSource $Tauon.Repo $Tauon.Msys) }
	$missing = @(Get-MissingMsysPackages $Tauon)
	if ($missing.Count -gt 0) {
		Fail ("Still missing after reinstalling: $($missing -join ', '). Open 'MSYS2 CLANG64' from the Start menu, " +
			"run: pacman -Syu   then double-click tauon-mirror.cmd again.")
	}
	Say 'Repaired.'
}

function Find-Python($Tauon) {
	$candidates = @()
	if ($Python) { $candidates += , @($Python) }
	if ($Tauon.Source) { $candidates += , @(Join-Path $Tauon.Repo '.venv\bin\python.exe') }
	$py = Get-Command py.exe -ErrorAction SilentlyContinue
	if ($py) { $candidates += , @($py.Source, '-3') }
	foreach ($cmd in @(Get-Command python.exe, python3.exe -All -ErrorAction SilentlyContinue)) {
		# Skip the Microsoft Store placeholder, which opens the Store instead of running Python
		if ($cmd.Source -notlike '*\WindowsApps\*') { $candidates += , @($cmd.Source) }
	}
	if ($Tauon.Msys) { $candidates += , @(Join-Path $Tauon.Msys 'clang64\bin\python.exe') }
	foreach ($candidate in $candidates) {
		$exe = $candidate[0]
		if (-not (Test-Path -LiteralPath $exe)) { continue }
		if ($Tauon.Msys -and $exe.StartsWith($Tauon.Msys, [StringComparison]::OrdinalIgnoreCase)) { Add-MsysToPath $Tauon.Msys }
		if ($Tauon.Source -and $exe.StartsWith($Tauon.Repo, [StringComparison]::OrdinalIgnoreCase)) { Add-MsysToPath $Tauon.Msys }
		$prefix = @($candidate | Select-Object -Skip 1)
		$code = Invoke-Native $exe ($prefix + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'))
		if ($code -eq 0) { return @{ Exe = $exe; Prefix = $prefix } }
	}
	Fail ("No Python 3.9 or newer was found to run the mirror. Install Python from https://www.python.org/downloads/windows/ " +
		"(tick 'Add python.exe to PATH'), or pass -Python C:\path\to\python.exe.")
}

function Install-StartupShortcut {
	$forward = @('-NoPrompt')
	if ($Device) { $forward += @('-Device', (ConvertTo-Argument $Device)) }
	if ($Tolerance -gt 0) { $forward += @('-Tolerance', $Tolerance.ToString([Globalization.CultureInfo]::InvariantCulture)) }
	if ($JellyfinUrl -and $JellyfinUrl -ne $env:JELLYFIN_URL) { $forward += @('-JellyfinUrl', (ConvertTo-Argument $JellyfinUrl)) }
	if ($NoMute) { $forward += '-NoMute' }
	if ($TauonExe) { $forward += @('-TauonExe', (ConvertTo-Argument $TauonExe)) }
	if ($TauonConf) { $forward += @('-TauonConf', (ConvertTo-Argument $TauonConf)) }
	if ($UseInstalled) { $forward += '-UseInstalled' }
	if ($Msys2Root) { $forward += @('-Msys2Root', (ConvertTo-Argument $Msys2Root)) }
	if ($Python) { $forward += @('-Python', (ConvertTo-Argument $Python)) }
	if ($VerboseMirror) { $forward += '-Verbose' }

	if ($JellyfinApiKey -and $JellyfinApiKey -ne [Environment]::GetEnvironmentVariable('JELLYFIN_API_KEY', 'User')) {
		# Kept out of the shortcut, where any program could read it from the command line
		if (Ask-YesNo 'Save the Jellyfin API key for your Windows user (JELLYFIN_API_KEY), so the login shortcut can use it?' $false) {
			[Environment]::SetEnvironmentVariable('JELLYFIN_API_KEY', $JellyfinApiKey, 'User')
			Say 'Saved JELLYFIN_API_KEY for your user.'
		} else {
			Warn 'The login shortcut will use the Jellyfin login set up in Tauon instead.'
		}
	}

	$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
	$shell = New-Object -ComObject WScript.Shell
	$shortcut = $shell.CreateShortcut($ShortcutPath)
	$shortcut.TargetPath = $powershell
	$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File ' +
		(ConvertTo-Argument $PSCommandPath) + ' ' + ($forward -join ' ')
	$shortcut.WorkingDirectory = $PSScriptRoot
	$shortcut.WindowStyle = 7  # minimised
	$shortcut.Description = 'Start Tauon and let it follow the phone (Jellyfin)'
	$shortcut.Save()
	Say "Tauon Mirror will start when you log in (shortcut: $ShortcutPath)."
}

function Start-Mirror($Tauon, [string]$Conf) {
	Step 'Starting the mirror'
	$pythonExe = Find-Python $Tauon
	$arguments = @($pythonExe.Prefix) + @($MirrorScript, '--tauon-conf', $Conf,
		'--log-file', (Join-Path $StateDir 'tauon-mirror.log'))
	if ($Device) { $arguments += @('--device', $Device) }
	if ($Tolerance -gt 0) { $arguments += @('--tolerance', $Tolerance.ToString([Globalization.CultureInfo]::InvariantCulture)) }
	if ($JellyfinUrl) { $arguments += @('--jellyfin-url', $JellyfinUrl) }
	if ($NoMute) { $arguments += '--no-mute' }
	if ($VerboseMirror) { $arguments += '-v' }
	# In the environment rather than on the command line, which other programs can read
	if ($JellyfinApiKey) { $env:JELLYFIN_API_KEY = $JellyfinApiKey }

	Say "Python: $($pythonExe.Exe)"
	Say "Log file: $(Join-Path $StateDir 'tauon-mirror.log')"
	Say 'Tauon now follows your phone. Press Ctrl+C or close this window to stop.'
	Write-Host ''

	return Invoke-Native $pythonExe.Exe $arguments
}

# --- Main -------------------------------------------------------------------

$VerboseMirror = $PSBoundParameters.ContainsKey('Verbose')
$conf = $null

try {
	if ($RemoveStartup) {
		if (Test-Path -LiteralPath $ShortcutPath) {
			Remove-Item -LiteralPath $ShortcutPath
			Say 'Removed the login shortcut. Tauon Mirror will no longer start when you log in.'
		} else {
			Say 'There is no login shortcut to remove.'
		}
		exit 0
	}

	Write-Host 'Tauon Mirror: Tauon shows what your phone plays through Jellyfin.'
	Step 'Finding Tauon'
	$tauon = Select-Tauon

	$apiReady = Test-TauonApi
	$running = @(Get-TauonProcess)
	if ($apiReady) {
		Say 'Tauon is running and its remote control is on.'
	} elseif ($running.Count -gt 0) {
		# Tauon is open: use that one, and don't touch tauon.conf, which it rewrites on exit
		$runningPath = $running[0].Path
		if ($runningPath -and -not $TauonExe) { $tauon.Exe = $runningPath }
		$conf = if ($TauonConf) { $TauonConf } else { Join-Path (Get-TauonUserDir $tauon.Exe) 'tauon.conf' }
		if ((Get-RemoteSetting $conf) -eq $true) {
			Say "Tauon is starting, waiting for its remote control (up to $TauonTimeout s)"
			$apiReady = Wait-TauonApi $TauonTimeout $true
		}
		if (-not $apiReady) {
			Warn "Tauon is open, but its remote control is off. It can only be turned on while Tauon is closed."
			Say 'Close Tauon now. If it hides in the system tray, right-click its tray icon and choose Exit.'
			if (-not (Test-CanAsk)) { Fail 'Tauon must be closed first (or turn on Settings > Connect > Sharing > Enable remote control and restart Tauon).' }
			Say 'Waiting for Tauon to close...'
			while (@(Get-TauonProcess).Count -gt 0) { Start-Sleep -Milliseconds 500 }
			Say 'Tauon is closed.'
		}
	}

	if (-not $apiReady) {
		if ($tauon.Source -and -not $SkipBuild) {
			if (-not (Build-FromSource $tauon.Repo $tauon.Msys)) {
				$installed = Find-InstalledTauon
				if ($installed -and (Ask-YesNo "Building Tauon failed (see the messages above). Use the installed Tauon ($installed) instead?")) {
					$tauon = @{ Exe = $installed; Source = $false; Msys = $tauon.Msys }
				} else {
					Fail 'Building Tauon failed, see the messages above. Running this again often helps after an MSYS2 update.'
				}
			}
		}
		if (-not (Test-Path -LiteralPath $tauon.Exe)) {
			Fail "Tauon was not found at $($tauon.Exe). Run this again without -SkipBuild to build it."
		}

		Step 'Turning on Tauon''s remote control'
		$conf = if ($TauonConf) { $TauonConf } else { Join-Path (Get-TauonUserDir $tauon.Exe) 'tauon.conf' }
		if ((Get-RemoteSetting $conf) -eq $true) {
			Say "Already on in $conf"
		} else {
			Enable-RemoteSetting $conf
			Say "Turned on in $conf"
		}

		Step 'Starting Tauon'
		if ($tauon.Source) {
			Repair-MsysDlls $tauon
			Add-MsysToPath $tauon.Msys
		}
		$workDir = if ($tauon.Source) { $tauon.Repo } else { Split-Path -Parent $tauon.Exe }
		$process = Start-Process -FilePath $tauon.Exe -WorkingDirectory $workDir -PassThru
		$null = $process.Handle  # keeps ExitCode readable after it ends
		Say "Waiting for Tauon's remote control to answer (up to $TauonTimeout s)"
		Say 'If Windows asks whether to allow Tauon through the firewall, either answer works for this.'
		if (-not (Wait-TauonApi $TauonTimeout $true)) {
			if (@(Get-TauonProcess).Count -eq 0) {
				# 0xC0000135: Windows could not find a DLL (SDL3.dll, libpython3.x.dll, libc++.dll, ...)
				if ($process.HasExited -and $process.ExitCode -eq -1073741515) {
					Fail ("Tauon could not start because Windows did not find some of its DLLs. They come from MSYS2: " +
						"check that $(if ($tauon.Msys) { Join-Path $tauon.Msys 'clang64\bin' } else { 'C:\msys64\clang64\bin' }) exists, " +
						"then run: tauon-mirror.cmd -Rebuild")
				}
				$userDir = Get-TauonUserDir $tauon.Exe
				$byHand = if ($tauon.Source) { Join-Path $PSScriptRoot 'start-tauon.cmd' } else { $tauon.Exe }
				Fail ("Tauon closed right after starting. Start it by hand to see what happens: $byHand " +
					"A crash report may be in $userDir\tauon-crash.log")
			}
			Fail ("Tauon is running but its remote control does not answer at $ApiUrl. " +
				"In Tauon, check Settings > Connect > Sharing > Enable remote control, then close Tauon and run this again. " +
				"Another program using port 7814 can also cause this.")
		}
		Say 'Tauon is ready.'
	}

	if (-not $conf) {
		$runningNow = @(Get-TauonProcess)
		if ($runningNow.Count -gt 0 -and $runningNow[0].Path -and -not $TauonExe) { $tauon.Exe = $runningNow[0].Path }
		$conf = if ($TauonConf) { $TauonConf } else { Join-Path (Get-TauonUserDir $tauon.Exe) 'tauon.conf' }
	}

	if ($InstallStartup) {
		Step 'Setting up start at login'
		Install-StartupShortcut
	}

	$code = Start-Mirror $tauon $conf
	if ($code -eq 2) {
		Fail ("The mirror found no Jellyfin login. In Tauon, set up your Jellyfin server (Settings > Connect > Services > Jellyfin), " +
			"or run this with -JellyfinUrl http://your-server:8096 -JellyfinApiKey YOUR_KEY.")
	}
	exit $code
} catch {
	$message = $_.Exception.Message
	Write-Host ''
	if ($message.StartsWith($ProblemMarker)) {
		Write-Host "PROBLEM: $($message.Substring($ProblemMarker.Length))" -ForegroundColor Red
	} else {
		Write-Host "UNEXPECTED ERROR: $message" -ForegroundColor Red
		Write-Host $_.ScriptStackTrace
	}
	exit 1
}
