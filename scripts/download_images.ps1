# Downloads the three mn-data-prepare images from GHCR (public, no login
# needed) into local Docker, so this "images" folder no longer needs to
# carry the multi-GB tarballs around -- just this script + RUN_COMMANDS.md.
#
# Usage: powershell -ExecutionPolicy Bypass -File download_images.ps1
#
# If you see "unauthorized" / "denied" errors below, the GHCR packages are
# still private -- see RUN_COMMANDS.md for how to make them public, or run
# `docker login ghcr.io` with a token that has read:packages first.

$ErrorActionPreference = "Stop"
$images = @(
    "ghcr.io/neith-san/mn-dataprep-coordinator:latest",
    "ghcr.io/neith-san/mn-dataprep-worker:latest",
    "ghcr.io/neith-san/mn-dataprep-ollama:latest"
)

foreach ($img in $images) {
    Write-Host "=== Pulling $img ==="
    docker pull $img
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED to pull $img -- see note above about private packages." -ForegroundColor Red
        exit 1
    }
}

# Retag to the short local names RUN_COMMANDS.md's docker run commands use,
# so nothing else needs to change based on where the image came from
# (ghcr.io pull vs. a loaded tarball).
docker tag ghcr.io/neith-san/mn-dataprep-coordinator:latest mn-dataprep-coordinator:latest
docker tag ghcr.io/neith-san/mn-dataprep-worker:latest mn-dataprep-worker:latest
docker tag ghcr.io/neith-san/mn-dataprep-ollama:latest mn-dataprep-ollama:latest

Write-Host ""
Write-Host "Done. Images ready as mn-dataprep-coordinator:latest, mn-dataprep-worker:latest, mn-dataprep-ollama:latest" -ForegroundColor Green
Write-Host "Continue with the 'Each worker PC' (or 'Manager') commands in RUN_COMMANDS.md."
