<#
.SYNOPSIS
Export the local Postiz (database + uploaded files) for the move to the cloud VM.

.DESCRIPTION
Run on the laptop, with the local Postiz stack running and NOTHING scheduled in it (Temporal,
which holds scheduled jobs, starts fresh on the VM). Writes two files to the output folder:

  postiz-db.dump        pg_dump of the Postiz database (custom format): accounts, connected
                        channels and their tokens, posts and their ids (Media OS needs these ids
                        to keep reading stats for posts already published)
  postiz-uploads.tgz    Postiz's uploads volume

Both contain account tokens: keep them private and delete them once the VM is running. Nothing
secret is printed.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File export-postiz.ps1 -OutDir "C:\Media OS Agent\postiz-export"
#>
[CmdletBinding()]
param(
    [string]$OutDir = 'C:\Media OS Agent\postiz-export',
    [string]$DbContainer = 'postiz-runtime-postgres',
    [string]$AppContainer = 'postiz-runtime-app',
    [string]$DbUser = 'postiz-user',
    [string]$DbName = 'postiz-db-local'
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "Checking nothing is still scheduled in Postiz..."
$scheduled = docker exec $DbContainer psql -U $DbUser -d $DbName -At -c `
    "SELECT count(*) FROM ""Post"" WHERE ""deletedAt"" IS NULL AND state = 'QUEUE' AND ""publishDate"" > now()"
if ([int]$scheduled -gt 0) {
    throw "$scheduled post(s) are still scheduled in the local Postiz. Move after the last one has gone out (or cancel and re-schedule them after the move)."
}

Write-Host "Dumping the Postiz database..."
docker exec $DbContainer pg_dump -U $DbUser -d $DbName -Fc -f /tmp/postiz-db.dump
docker cp "${DbContainer}:/tmp/postiz-db.dump" (Join-Path $OutDir 'postiz-db.dump')
docker exec $DbContainer rm -f /tmp/postiz-db.dump

Write-Host "Packing Postiz uploads..."
docker exec $AppContainer sh -c 'tar -czf /tmp/postiz-uploads.tgz -C /uploads .'
docker cp "${AppContainer}:/tmp/postiz-uploads.tgz" (Join-Path $OutDir 'postiz-uploads.tgz')
docker exec $AppContainer rm -f /tmp/postiz-uploads.tgz

Get-ChildItem $OutDir | Select-Object Name, Length | Format-Table -AutoSize
Write-Host "Done. Copy these two files, plus postiz-runtime\docker-compose.yaml and postiz-runtime\.env, to the VM over Tailscale (README step 4)."
