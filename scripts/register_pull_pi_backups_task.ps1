#Requires -Version 5.1
<#
.SYNOPSIS
    Register the SP-363 Windows_Pull_Task (scripts/pull_pi_backups.ps1) with
    Windows Task Scheduler so off-Pi retention runs automatically.

.DESCRIPTION
    Companion to scripts/pull_pi_backups.ps1 (task 4.1). This script registers
    that pull script as a scheduled task that runs at a cadence AT LEAST as
    frequent as the weekly Pi backup (SP-363 Req 2.2).

    Cadence choice (why DAILY is the default):
        The Pi Backup_Job produces a new Dump_Artifact weekly. The design only
        requires the Windows_Pull_Task to run at a cadence "at least as frequent
        as the weekly backup". A WEEKLY pull is the minimum, but a single missed
        weekly cycle (PC asleep, network down, Pi offline) could delay off-Pi
        durability by a full week. A DAILY pull is strictly >= weekly, is cheap
        (the pull is idempotent and skips already-retained artifacts with a
        verified-equal size), and tolerates missed cycles: the next successful
        run catches up on every not-yet-retained artifact, including any from a
        preceding weekly backup it had not yet seen. DAILY is therefore the safe
        default. WEEKLY (and an explicit custom time) remain selectable via
        -Schedule for operators who prefer to track the backup cadence exactly.

    The registered action runs:
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File <full path to
        pull_pi_backups.ps1> -SourcePath <...> -LocalPath <...>
        [-ConnectionTimeoutSeconds <...>] [-KeepCount <...>]

    Any existing task of the same name is overwritten (-Force on
    Register-ScheduledTask), so re-running this script updates the schedule in
    place rather than erroring.

    Credential note (reading the Pi share as `mypi`):
        pull_pi_backups.ps1 reads the [bf_backups] Samba share as user `mypi`.
        This registration script does NOT embed the `mypi` share password in the
        scheduled task. Supply the `mypi` credential to the pull securely via one
        of:
          * Run the task as a Windows account that already has the Pi share
            credentials cached (cmdkey /add:<pi-host> /user:mypi /pass:...),
            so pull_pi_backups.ps1 uses the current session credentials. This
            is the recommended default and needs no secret in the task.
          * Store a credential with the Windows Credential Manager / a protected
            (Export-Clixml, DPAPI-encrypted, per-user) file that the pull script
            is extended to read, rather than passing -Credential on the command
            line.
        Do NOT hard-code the password here or in the task action arguments:
        scheduled-task arguments are recoverable in plain text.

.PARAMETER SourcePath
    UNC path to the [bf_backups] Samba share on the Pi, e.g.
    \\raspberrypi\bf_backups or \\192.168.1.50\bf_backups. Passed through to
    pull_pi_backups.ps1 -SourcePath. Required.

.PARAMETER LocalPath
    Durable Windows directory where Off_Pi_Copies are retained. Passed through
    to pull_pi_backups.ps1 -LocalPath. Required.

.PARAMETER Schedule
    Pull cadence. Must be at least as frequent as the weekly backup. One of
    'Daily' (default, safe) or 'Weekly'. Daily is recommended because it is
    strictly >= weekly and tolerates missed cycles (see .DESCRIPTION).

.PARAMETER At
    Time of day the pull runs, as a parseable time string (e.g. '04:00',
    '4:00 AM'). Default '04:00' — an hour after the Pi's Sunday 03:00 backup so
    a fresh weekly dump is available to pull. Applies to both Daily and Weekly.

.PARAMETER DaysOfWeek
    Day(s) the pull runs when -Schedule is 'Weekly'. Ignored for 'Daily'.
    Default 'Sunday' (shortly after the weekly backup lands). Accepts one or
    more System.DayOfWeek values.

.PARAMETER TaskName
    Name to register the task under. Default 'SP-363 Pull Pi Backups'.
    Re-running with the same name overwrites the existing task.

.PARAMETER PullScriptPath
    Full path to pull_pi_backups.ps1. Defaults to the sibling file next to this
    registration script, so it works from a checkout without extra arguments.

.PARAMETER KeepCount
    Optional off-Pi retention count passed through to pull_pi_backups.ps1
    -KeepCount (minimum most-recent local copies to keep). Must be >= 1. When
    omitted, the pull script's own default (3) applies.

.PARAMETER ConnectionTimeoutSeconds
    Optional share connection timeout passed through to pull_pi_backups.ps1
    -ConnectionTimeoutSeconds. 1..3600. When omitted, the pull script's own
    default (30) applies.

.PARAMETER RunAsUser
    Optional Windows user to run the task as (principal). Defaults to the
    current user. Use an account that has the `mypi` share credentials cached
    (see the credential note in .DESCRIPTION).

.EXAMPLE
    # Safe default: daily pull at 04:00 as the current user.
    .\register_pull_pi_backups_task.ps1 -SourcePath \\raspberrypi\bf_backups `
        -LocalPath D:\pi_backups

.EXAMPLE
    # Minimum cadence: weekly on Sunday at 04:00, keeping 6 local copies.
    .\register_pull_pi_backups_task.ps1 -SourcePath \\192.168.1.50\bf_backups `
        -LocalPath D:\pi_backups -Schedule Weekly -DaysOfWeek Sunday -At '04:00' `
        -KeepCount 6

.NOTES
    SP-363 pi-postgres-backup-automation, task 4.2.
    Satisfies Req 2.2 (Windows_Pull_Task registered at a cadence at least as
    frequent as the weekly backup). The pull logic itself lives in task 4.1's
    scripts/pull_pi_backups.ps1.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalPath,

    [Parameter(Mandatory = $false)]
    [ValidateSet('Daily', 'Weekly')]
    [string]$Schedule = 'Daily',

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$At = '04:00',

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [System.DayOfWeek[]]$DaysOfWeek = @([System.DayOfWeek]::Sunday),

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'SP-363 Pull Pi Backups',

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$PullScriptPath = (Join-Path -Path $PSScriptRoot -ChildPath 'pull_pi_backups.ps1'),

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$KeepCount,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 3600)]
    [int]$ConnectionTimeoutSeconds,

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$RunAsUser = ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
)

# Fail fast: any un-caught error terminates rather than leaving a half-registered
# task behind.
$ErrorActionPreference = 'Stop'

# --- Validate the pull script exists and resolve it to a full path ----------
# The scheduled action must reference an absolute path: Task Scheduler runs with
# an unpredictable working directory, so a relative path would not resolve.
if (-not (Test-Path -LiteralPath $PullScriptPath -PathType Leaf)) {
    throw "Pull script not found at '$PullScriptPath'. Pass -PullScriptPath to the full path of pull_pi_backups.ps1 (task 4.1)."
}
$pullScriptFull = (Resolve-Path -LiteralPath $PullScriptPath).ProviderPath

# --- Parse the run time -----------------------------------------------------
try {
    $runAt = [datetime]::Parse($At)
}
catch {
    throw "Could not parse -At value '$At' as a time of day (try '04:00' or '4:00 AM'): $($_.Exception.Message)"
}

# --- Build the action argument list -----------------------------------------
# Run powershell.exe with -NoProfile -ExecutionPolicy Bypass -File <pull script>,
# then the pull script's own parameters. Each path is double-quoted so paths with
# spaces survive the scheduler's command-line parsing.
$argList = @(
    '-NoProfile'
    '-ExecutionPolicy', 'Bypass'
    '-File', ('"{0}"' -f $pullScriptFull)
    '-SourcePath', ('"{0}"' -f $SourcePath)
    '-LocalPath', ('"{0}"' -f $LocalPath)
)
# Only pass the pass-through tunables when the caller set them, so the pull
# script's own defaults (KeepCount=3, ConnectionTimeoutSeconds=30) otherwise apply.
if ($PSBoundParameters.ContainsKey('KeepCount')) {
    $argList += @('-KeepCount', $KeepCount)
}
if ($PSBoundParameters.ContainsKey('ConnectionTimeoutSeconds')) {
    $argList += @('-ConnectionTimeoutSeconds', $ConnectionTimeoutSeconds)
}
$argumentString = $argList -join ' '

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentString

# --- Build the trigger at a cadence at least as frequent as weekly ----------
switch ($Schedule) {
    'Daily' {
        # Strictly >= weekly and tolerant of missed cycles (the safe default).
        $trigger = New-ScheduledTaskTrigger -Daily -At $runAt
    }
    'Weekly' {
        # The minimum acceptable cadence; tracks the weekly backup exactly.
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At $runAt
    }
}

# --- Settings: be resilient to a PC that was asleep/off at trigger time -----
# StartWhenAvailable makes the scheduler run a missed trigger as soon as it can,
# which pairs with the pull's idempotent catch-up behaviour.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# --- Principal: run as the chosen user with least privilege -----------------
# Limited run level is sufficient: the pull only reads the share and writes to a
# local durable path. Running as a user with the `mypi` share credentials cached
# avoids embedding any secret in the task (see credential note in .DESCRIPTION).
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Interactive -RunLevel Limited

$description = "SP-363 Windows_Pull_Task: pull Pi Postgres Dump_Artifacts off the Pi to '$LocalPath' for off-Pi retention. Cadence: $Schedule at $($runAt.ToString('HH:mm')). Pull logic in pull_pi_backups.ps1."

# --- Register (overwrite any existing task of the same name) ----------------
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

$cadenceDetail = if ($Schedule -eq 'Weekly') {
    "Weekly on $($DaysOfWeek -join ', ') at $($runAt.ToString('HH:mm'))"
}
else {
    "Daily at $($runAt.ToString('HH:mm'))"
}

Write-Host "Registered scheduled task '$TaskName'."
Write-Host "  Cadence : $cadenceDetail (at least as frequent as the weekly backup)."
Write-Host "  Runs    : powershell.exe $argumentString"
Write-Host "  As user : $RunAsUser (Interactive, Limited)."
Write-Host ""
Write-Host "Credential reminder: ensure this user can read the Pi share as 'mypi'"
Write-Host "(e.g. cmdkey, or Credential Manager) rather than embedding a password in the task."
