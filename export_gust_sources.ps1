# Export-GustSources.ps1
#
# Exportiert alle Python-Dateien aus dem GUST-Verzeichnis
# in ein TXT-File für den Upload in das Claude Projekt-Wissen.
#
# Ausführen aus beliebigem Verzeichnis:
#   .\Export-GustSources.ps1
#
# Optionale Parameter:
#   -SourceDir    Quellverzeichnis    (default: E:\Oszi_plus_Protokolle\GUST)
#   -OutputFile   Ausgabedatei        (default: gust_sources.txt im SourceDir)
#   -SkipTests    Test-Dateien überspringen (default: false)
#
# Enthaltene Dateien:
#   *.py          Alle Python-Dateien direkt im GUST-Verzeichnis
#   **/*.py       Alle Python-Dateien in Unterverzeichnissen

param(
    [string] $SourceDir  = "E:\Oszi_plus_Protokolle\GUST",
    [string] $OutputFile = "",
    [switch] $SkipTests  = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Ausgabedatei bestimmen
if ($OutputFile -eq "") {
    $OutputFile = Join-Path $SourceDir "gust_sources.txt"
}

# Prüfen ob Quellverzeichnis existiert
if (-not (Test-Path $SourceDir)) {
    Write-Error "Quellverzeichnis nicht gefunden: $SourceDir"
    exit 1
}

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$gitBranch = ""
$gitHash   = ""
try {
    Push-Location $SourceDir
    $gitBranch = & git rev-parse --abbrev-ref HEAD 2>$null
    $gitHash   = & git rev-parse --short HEAD       2>$null
    Pop-Location
} catch { }

$gitLine = if ($gitBranch) { "# Branch    : $gitBranch  ($gitHash)" } else { "" }

$header = @"
# =============================================================================
# GUST - Python Source Export für Claude Projekt-Wissen
# Generiert  : $timestamp
# Verzeichnis: $SourceDir
$gitLine
# =============================================================================
#
# Enthält alle *.py Dateien aus dem GUST-Verzeichnis.
# Jede Datei wird durch eine Trennzeile eingeleitet:
#   # === <relativer Pfad> ===
#
# =============================================================================

"@

# ---------------------------------------------------------------------------
# Python-Dateien sammeln
# ---------------------------------------------------------------------------

$priorityOrder = @(
    "gust.py",
    "gust_audio.py",
    "gust_rx.py",
    "gust_modulator.py",
    "gust_frame.py",
    "gust_decode.py",
    "gust_hackrf.py",
    "gust_eventbus.py",
    "gust_web.py"
)

$pyFiles = Get-ChildItem -Path $SourceDir -Recurse -Filter "*.py" |
    Where-Object {
        if ($_.FullName -eq $OutputFile) { return $false }
        if ($SkipTests -and $_.FullName -match "[\\/]tests?[\\/]") { return $false }
        if ($SkipTests -and $_.Name -match "^test_") { return $false }
        return $true
    } |
    Sort-Object {
        $rel   = $_.FullName.Replace($SourceDir, "").TrimStart("\\/")
        $depth = ($rel -split "[\\/]").Count
        $prio  = 99
        for ($i = 0; $i -lt $priorityOrder.Count; $i++) {
            if ($_.Name -eq $priorityOrder[$i]) { $prio = $i; break }
        }
        "{0:D2}_{1:D2}_{2}" -f $depth, $prio, $rel
    }

# ---------------------------------------------------------------------------
# Dateien zusammenbauen
# ---------------------------------------------------------------------------

$seen     = @{}
$lines    = [System.Collections.Generic.List[string]]::new()
$included = 0
$skipped  = 0

$lines.Add($header)

foreach ($file in $pyFiles) {
    $absPath = $file.FullName
    $relPath = $absPath.Replace($SourceDir, "").TrimStart("\\/").Replace("\", "/")

    if ($seen.ContainsKey($relPath)) {
        $skipped++
        continue
    }
    $seen[$relPath] = $true

    $content = (Get-Content $absPath -Raw -Encoding UTF8) `
        -replace "`r`n", "`n" `
        -replace "`r",   "`n"

    $lines.Add("# === $relPath ===")
    $lines.Add($content)
    $lines.Add("")
    $included++
}

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

$footer = @"
# =============================================================================
# Ende des Exports
# Enthaltene Dateien : $included
# Übersprungen       : $skipped  (Duplikate / Testdateien)
# =============================================================================
"@
$lines.Add($footer)

# ---------------------------------------------------------------------------
# Schreiben: UTF-8 ohne BOM, LF-Zeilenenden
# ---------------------------------------------------------------------------

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText(
    $OutputFile,
    ($lines -join "`n"),
    $utf8NoBom
)

Write-Host ""
Write-Host "OK  Exportiert nach: $OutputFile"
Write-Host "    Gefundene .py-Dateien : $(([array]$pyFiles).Count)"
Write-Host "    Exportiert            : $included"
Write-Host "    Übersprungen          : $skipped"
Write-Host ""