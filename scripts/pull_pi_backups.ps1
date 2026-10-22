#Requires -Version 5.1
<#
.SYNOPSIS
    Pull Postgres Dump_Artifacts off the Raspberry Pi to durable Windows
    storage (SP-363 Windows_Pull_Task, off-Pi retention).

.DESCRIPTION
    Reads the [bf_backups] Samba share on the Pi (as user `mypi`) and copies
    every Dump_Artifact that is not already retained locally with a
    verified-equal byte size to an operator-chosen durable Windows path.

    This is the off-Pi durability half of the pi-postgres-backup-automation
    design. It is PULL-ONLY: the Pi is a passive source and never initiates
    the transfer, and this script NEVER deletes, overwrites, or modifies any
    artifact on the Pi. The only files this script removes are its own local
    Off_Pi_Copies, under the independent off-Pi retention policy.

    A Dump_Artifact is named `bf_trader_<UTC-timestamp>.dump`, where the
    timestamp is the fixed-width, lexicographically sortable UTC form
    `YYYYMMDDThhmmssZ` (e.g. bf_trader_20260115T030000Z.dump). The same pattern
    is used by the Pi-side retention core (scripts/backup_retention.py) so
    newest-N selection matches between the Pi and the Windows copies.

    Behaviour (maps to requirements):
      * Connect to the share within -ConnectionTimeoutSeconds; on failure mark
        the cycle failed and touch nothing on the Pi (Req 4.2).
      * Enumerate Dump_Artifacts and copy every one not yet retained locally,
        including any from preceding missed cycles (Req 2.2, 4.3).
      * After each copy, verify local byte size == source byte size. On
        mismatch or interrupted copy, treat the artifact as NOT retained,
        remove the partial local file, keep the Pi source authoritative, and
        surface the failure (Req 2.3, 2.4, 4.6).
      * Skip artifacts already retained with a verified-equal size, so repeated
        runs produce no duplicates (idempotent) (Req 2.5).
      * Apply an independent off-Pi retention policy keeping at least the N most
        recent local copies (default 3), independent of the Pi keep-last-3
        (Req 2.6).

.PARAMETER SourcePath
    UNC path to the [bf_backups] Samba share on the Pi, e.g.
    \\raspberrypi\bf_backups. Required.

.PARAMETER LocalPath
    Durable Windows directory where Off_Pi_Copies are retained. Created if it
    does not exist. Required.

.PARAMETER Credential
    Optional PSCredential for the share (user `mypi`). If omitted, the current
    Windows session credentials are used. Supply this to authenticate as
    `mypi` when the session is not already authorised.

.PARAMETER ConnectionTimeoutSeconds
    Maximum seconds to wait when establishing/reaching the share before marking
    the cycle failed. Default 30.

.PARAMETER KeepCount
    Off-Pi retention: the minimum number of most-recent local copies to keep.
    Must be >= 1. Default 3. This is independent of the Pi-side keep-last-3.

.EXAMPLE
    .\pull_pi_backups.ps1 -SourcePath \\raspberrypi\bf_backups -LocalPath D:\pi_backups

.EXAMPLE
    $cred = Get-Credential mypi
    .\pull_pi_backups.ps1 -SourcePath \\192.168.1.50\bf_backups -LocalPath D:\pi_backups `
        -Credential $cred -ConnectionTimeoutSeconds 20 -KeepCount 6

.OUTPUTS
    Writes a structured summary to the pipeline. Exits 0 when every present
    artifact is retained with a verified-equal size; exits non-zero when the
    share is unreachable or any artifact failed to copy/verify this cycle.

.NOTES
    SP-363 pi-postgres-backup-automation, task 4.1.
    Task Scheduler registration is the separate task 4.2 (not done here).
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
    [System.Management.Automation.PSCredential]$Credential,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 3600)]
    [int]$ConnectionTimeoutSeconds = 30,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$KeepCount = 3
)

# Fail fast: any un-caught error terminates rather than silently continuing a
# partial cycle. Per-copy errors are caught locally so one bad artifact does
# not abort retention of the good ones.
$ErrorActionPreference = 'Stop'

# A Dump_Artifact is `bf_trader_<YYYYMMDDThhmmssZ>.dump`. Anchored and specific
# so the pull never matches unrelated files on the share. Mirrors the Pi-side
# retention core (_DUMP_NAME_RE in scripts/backup_retention.py).
$script:DumpNamePattern = '^bf_trader_\d{8}T\d{6}Z\.dump$'

function Write-Log {
    <#
    .SYNOPSIS
        Emit a timestamped, levelled log line so a scheduled run leaves an
        auditable trace (errors on the error stream, the rest on information).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
    if ($Level -eq 'ERROR') {
        Write-Error $line -ErrorAction Continue
    }
    elseif ($Level -eq 'WARN') {
        Write-Warning $line
    }
    else {
        Write-Information $line -InformationAction Continue
    }
}

function Test-ShareReachable {
    <#
    .SYNOPSIS
        Wait up to -TimeoutSeconds for the share to become reachable. Returns
        $true if reachable within the window, $false otherwise. Reaching the
        share is read-only and never modifies the Pi (Req 4.2).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            if (Test-Path -LiteralPath $Path) {
                return $true
            }
        }
        catch {
            # Transient SMB/name-resolution hiccup; keep trying until deadline.
            Write-Verbose ("Share not yet reachable: {0}" -f $_.Exception.Message)
        }
        if ((Get-Date) -ge $deadline) {
            break
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Copy-DumpArtifact {
    <#
    .SYNOPSIS
        Copy one Dump_Artifact from the share to the local store and verify the
        copied byte size equals the source byte size. On any failure the partial
        local file is removed and $false is returned, so a partial copy is never
        presented as retained (Req 2.3, 2.4, 4.6). The Pi source is read-only
        throughout and is left untouched.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][System.IO.FileInfo]$SourceFile,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $sourceSize = $SourceFile.Length
    try {
        # -Force overwrites any prior *local* partial of the same name only;
        # the source on the Pi is never written to.
        Copy-Item -LiteralPath $SourceFile.FullName -Destination $Destination -Force

        $localItem = Get-Item -LiteralPath $Destination
        if ($localItem.Length -ne $sourceSize) {
            Write-Log -Level ERROR -Message (
                "Size mismatch for {0}: source={1} bytes, local={2} bytes. Treating as NOT retained." `
                    -f $SourceFile.Name, $sourceSize, $localItem.Length)
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            return $false
        }
        Write-Log -Message ("Retained {0} ({1} bytes, size verified)." -f $SourceFile.Name, $sourceSize)
        return $true
    }
    catch {
        # Interrupted / failed copy: do not present a partial as retained.
        Write-Log -Level ERROR -Message (
            "Copy of {0} failed or was interrupted: {1}. Treating as NOT retained." `
                -f $SourceFile.Name, $_.Exception.Message)
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        }
        return $false
    }
}

function Invoke-OffPiRetention {
    <#
    .SYNOPSIS
        Apply the independent off-Pi retention policy: keep at least the
        -KeepCount most-recent local Off_Pi_Copies and delete older ones
        (Req 2.6). Ordering uses the embedded UTC timestamp via the lexically
        sortable filename, matching the Pi-side selection. Only local files are
        removed; the Pi is never touched. Delete failures are surfaced but do
        not abort the preserved set.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$LocalStore,
        [Parameter(Mandatory = $true)][int]$Keep
    )
    $localDumps = @(
        Get-ChildItem -LiteralPath $LocalStore -File |
            Where-Object { $_.Name -match $script:DumpNamePattern }
    )
    if ($localDumps.Count -le $Keep) {
        return
    }
    # Fixed-width sortable names => descending filename sort == newest first.
    $ordered = $localDumps | Sort-Object -Property Name -Descending
    $toDelete = $ordered | Select-Object -Skip $Keep
    foreach ($old in $toDelete) {
        try {
            Remove-Item -LiteralPath $old.FullName -Force
            Write-Log -Message ("Off-Pi retention: removed old local copy {0}." -f $old.Name)
        }
        catch {
            Write-Log -Level WARN -Message (
                "Off-Pi retention: failed to remove {0}: {1}" -f $old.Name, $_.Exception.Message)
        }
    }
}

function Invoke-Pull {
    <#
    .SYNOPSIS
        Run one Windows_Pull_Task cycle: connect, enumerate, copy+verify new
        artifacts, then apply off-Pi retention. Returns the number of artifacts
        that failed to copy/verify this cycle (0 == clean cycle).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Local,
        [Parameter(Mandatory = $true)][int]$Keep
    )

    # Enumerate Dump_Artifacts on the share (read-only).
    $artifacts = @(
        Get-ChildItem -LiteralPath $Source -File |
            Where-Object { $_.Name -match $script:DumpNamePattern } |
            Sort-Object -Property Name
    )
    Write-Log -Message ("Found {0} Dump_Artifact(s) on the share." -f $artifacts.Count)

    $copied = 0
    $skipped = 0
    $failed = 0

    foreach ($artifact in $artifacts) {
        $destination = Join-Path -Path $Local -ChildPath $artifact.Name

        # Idempotence: skip an artifact already retained with a verified-equal
        # size so repeated runs produce no duplicates (Req 2.5).
        if (Test-Path -LiteralPath $destination) {
            $existing = Get-Item -LiteralPath $destination
            if ($existing.Length -eq $artifact.Length) {
                $skipped++
                continue
            }
            Write-Log -Level WARN -Message (
                "Local copy of {0} exists but size differs (local={1}, source={2}); re-copying." `
                    -f $artifact.Name, $existing.Length, $artifact.Length)
        }

        if (Copy-DumpArtifact -SourceFile $artifact -Destination $destination) {
            $copied++
        }
        else {
            # Failed/interrupted/mismatched: NOT retained, Pi source stays
            # authoritative, surfaced via the error stream in Copy-DumpArtifact.
            $failed++
        }
    }

    Write-Log -Message ("Cycle copy summary: copied={0}, skipped={1}, failed={2}." -f $copied, $skipped, $failed)

    # Independent off-Pi retention (local-only pruning) runs regardless of
    # per-artifact failures; it only ever removes local copies.
    Invoke-OffPiRetention -LocalStore $Local -Keep $Keep

    return $failed
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
$mappedDrive = $null
try {
    Write-Log -Message ("Starting Windows pull: source='{0}' local='{1}' timeout={2}s keep={3}." `
            -f $SourcePath, $LocalPath, $ConnectionTimeoutSeconds, $KeepCount)

    # Ensure the durable local target exists before we copy into it.
    if (-not (Test-Path -LiteralPath $LocalPath)) {
        New-Item -ItemType Directory -Path $LocalPath -Force | Out-Null
        Write-Log -Message ("Created local durable store '{0}'." -f $LocalPath)
    }

    # When a credential is supplied, establish an authenticated connection to
    # the share (as `mypi`) via a temporary PSDrive. Without one we rely on the
    # current session's credentials.
    if ($Credential) {
        $mappedDrive = New-PSDrive -Name ([guid]::NewGuid().ToString('N')) `
            -PSProvider FileSystem -Root $SourcePath -Credential $Credential -Scope Script
        Write-Log -Message "Established authenticated connection to the share."
    }

    # Connection timeout: if the share is not reachable within the window, mark
    # the cycle failed and touch nothing on the Pi (Req 4.2).
    if (-not (Test-ShareReachable -Path $SourcePath -TimeoutSeconds $ConnectionTimeoutSeconds)) {
        Write-Log -Level ERROR -Message (
            "Share '{0}' not reachable within {1}s. Cycle failed; no Pi artifact touched." `
                -f $SourcePath, $ConnectionTimeoutSeconds)
        exit 2
    }

    $failedCount = Invoke-Pull -Source $SourcePath -Local $LocalPath -Keep $KeepCount

    if ($failedCount -gt 0) {
        Write-Log -Level ERROR -Message (
            "{0} artifact(s) failed to copy/verify this cycle; cycle marked failed." -f $failedCount)
        exit 1
    }

    Write-Log -Message "Pull cycle completed successfully; all present artifacts retained and verified."
    exit 0
}
catch {
    # Any unexpected failure: mark the cycle failed. We never delete/overwrite
    # anything on the Pi, so the Pi source remains the authoritative copy.
    Write-Log -Level ERROR -Message ("Pull cycle failed: {0}" -f $_.Exception.Message)
    exit 1
}
finally {
    if ($mappedDrive) {
        Remove-PSDrive -Name $mappedDrive.Name -Force -ErrorAction SilentlyContinue
    }
}
