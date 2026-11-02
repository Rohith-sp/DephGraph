# Runs a .sql file against the local MySQL server using the saved
# "dephgraph" login path, and writes the output to a log file.
#
#   .\scripts\run-sql.ps1 er-diagram\schema.sql
#   .\scripts\run-sql.ps1 scripts\verify_schema.sql -Log logs\verify.log
#
# The password is never passed here: save it once with
#   mysql_config_editor set --login-path=dephgraph --host=localhost --user=root --password

param(
    [Parameter(Mandatory)][string]$File,
    [string]$Log
)

$mysql = 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe'
$path  = (Resolve-Path $File).Path -replace '\\', '/'
if (-not $Log) { $Log = Join-Path 'logs' ([IO.Path]::GetFileNameWithoutExtension($File) + '.log') }
New-Item -ItemType Directory -Force (Split-Path $Log -Parent) | Out-Null

$output = & $mysql --login-path=dephgraph --table --default-character-set=utf8mb4 --execute "source $path" 2>&1
$code = $LASTEXITCODE
$output | ForEach-Object { "$_" } | Out-File -FilePath $Log -Encoding utf8
"exit code: $code" | Out-File -FilePath $Log -Encoding utf8 -Append

$output | ForEach-Object { "$_" }
if ($code -eq 0) { Write-Host "OK  -> $Log" -ForegroundColor Green }
else { Write-Host "FAILED (exit $code) -> $Log" -ForegroundColor Red }
