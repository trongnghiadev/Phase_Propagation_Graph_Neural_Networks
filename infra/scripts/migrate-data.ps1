<#
.SYNOPSIS
  Sao lưu database vietnam_gis từ MongoDB một node cũ (container gis-mongo) rồi nạp vào cụm mới.

.DESCRIPTION
  1. mongodump từ container nguồn ra file infra/backup/vietnam_gis.archive (gzip).
     Nếu file sao lưu đã có, dùng lại (thêm -FreshDump để dump lại).
  2. mongorestore vào cụm đích qua mạng Docker gis-dist-net:
       -Target rs     replica set rs0 (kịch bản A), có --drop: chỉ xóa collection trên CỤM MỚI
       -Target shard  sharded cluster qua mongos (kịch bản B), KHÔNG --drop để giữ
                      các collection đã được shard bởi init-shard.js
  3. So sánh số document từng collection giữa nguồn và đích.

  Container nguồn KHÔNG bị thay đổi.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File infra/scripts/migrate-data.ps1 -Target rs
#>
param(
  [ValidateSet("rs", "shard")] [string]$Target = "rs",
  [string]$Source = "gis-mongo",
  [string]$Db = "vietnam_gis",
  [switch]$FreshDump
)
$ErrorActionPreference = "Stop"
$backupDir = Join-Path $PSScriptRoot "..\backup" | Resolve-Path -ErrorAction SilentlyContinue
if (-not $backupDir) { $backupDir = (New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "..\backup")).FullName }
$archive = Join-Path $backupDir "$Db.archive"

$uri = if ($Target -eq "rs") {
  "mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0"
} else { "mongodb://mongos:27200" }

# ---- 1. Sao lưu
if ($FreshDump -or -not (Test-Path $archive)) {
  Write-Host "==> mongodump $Db từ container $Source"
  docker exec $Source mongodump --db $Db --archive=/tmp/$Db.archive --gzip
  if ($LASTEXITCODE -ne 0) { throw "mongodump thất bại" }
  docker cp "${Source}:/tmp/$Db.archive" $archive
  docker exec $Source rm -f /tmp/$Db.archive | Out-Null
} else {
  Write-Host "==> Dùng lại bản sao lưu có sẵn: $archive"
}
Write-Host ("    Kích thước: {0:N0} MB" -f ((Get-Item $archive).Length / 1MB))

# ---- 2. Nạp vào cụm đích
$restoreArgs = @("--uri", $uri, "--archive=/backup/$Db.archive", "--gzip",
                 "--nsInclude", "$Db.*", "--numInsertionWorkersPerCollection", "2")
if ($Target -eq "rs") { $restoreArgs += "--drop" }
Write-Host "==> mongorestore vào $Target ($uri)"
docker run --rm --network gis-dist-net -v "${backupDir}:/backup:ro" mongo:8.0 mongorestore @restoreArgs
if ($LASTEXITCODE -ne 0) { throw "mongorestore thất bại" }

# ---- 3. Đối chiếu số document
$countJs = "const d=db.getSiblingDB('$Db'); const o={}; d.getCollectionNames().filter(c=>!c.startsWith('system.')).sort().forEach(c=>o[c]=d.getCollection(c).countDocuments({})); print(JSON.stringify(o));"
$src = docker exec $Source mongosh --quiet --eval $countJs | ConvertFrom-Json
$dst = docker run --rm --network gis-dist-net mongo:8.0 mongosh $uri --quiet --eval $countJs | ConvertFrom-Json
Write-Host "==> Đối chiếu số document (nguồn → đích)"
$bad = 0
foreach ($p in $src.PSObject.Properties) {
  $d = $dst.($p.Name)
  $mark = if ($d -eq $p.Value) { "OK " } else { $bad++; "LỆCH" }
  Write-Host ("    {0} {1,-28} {2,8} → {3,8}" -f $mark, $p.Name, $p.Value, $d)
}
if ($bad -gt 0) { throw "$bad collection lệch số document" }
Write-Host "==> Hoàn tất, số document khớp."
