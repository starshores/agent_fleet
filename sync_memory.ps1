# sync_memory.ps1 — 把项目记忆全量硬链镜像到 agent_fleet\memory\
# 目的:只迁移 agent_fleet 文件夹即可带走全部记忆(复制文件夹会拷走硬链指向的真实内容)。
# 幂等:每次重建硬链(应对原子保存断链)+ 清理源已删的残链。同卷(C:)NTFS;不占额外空间。
# 用法:powershell -NoProfile -ExecutionPolicy Bypass -File C:\apphome\agent_fleet\sync_memory.ps1

$mem = Join-Path $env:USERPROFILE ".claude\projects\XXX\memory"
$dst = "C:\apphome\agent_fleet\memory"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$srcFiles = Get-ChildItem $mem -Filter *.md -File
foreach ($s in $srcFiles) {
  $lnk = Join-Path $dst $s.Name
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  New-Item -ItemType HardLink -Path $lnk -Value $s.FullName | Out-Null
}
# 清理:dst 里源已不存在的(已删记忆)
$srcNames = $srcFiles | ForEach-Object { $_.Name }
Get-ChildItem $dst -Filter *.md -File | Where-Object { $srcNames -notcontains $_.Name } | ForEach-Object {
  Remove-Item $_.FullName -Force; Write-Output ("剪除残链: " + $_.Name)
}
Write-Output ("镜像完成: " + $srcFiles.Count + " 个 .md 硬链 -> " + $dst)
