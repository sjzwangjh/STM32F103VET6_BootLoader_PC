# 一键为 winusb_dfm.inf 生成自签名 catalog 并安装测试证书
# 使用方法：右键“以管理员身份运行 PowerShell”，然后执行：
#   powershell -ExecutionPolicy Bypass -File sign_winusb_driver.ps1
$ErrorActionPreference = 'Stop'

$pc  = $PSScriptRoot
$inf = Join-Path $pc 'winusb_dfm.inf'
$cat = Join-Path $pc 'winusb_dfm.cat'

# 定位 WDK 工具（取最新的 10.0.xxxx 版本）
$kits = 'C:\Program Files (x86)\Windows Kits\10\bin'
$sig  = $null
$mk   = $null
Get-ChildItem $kits -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
    if (-not $sig) { $p = Join-Path $_.FullName 'x64\signtool.exe';  if (Test-Path $p) { $sig = $p } }
    if (-not $mk)  { $p = Join-Path $_.FullName 'x64\makecat.exe';   if (Test-Path $p) { $mk  = $p } }
}
if (-not $sig -or -not $mk) { throw 'signtool.exe / makecat.exe not found in Windows Kits.' }

Write-Host "signtool: $sig"
Write-Host "makecat : $mk"

# 1) 生成自签代码签名证书（LocalMachine 需要管理员）
$cert = New-SelfSignedCertificate -Type CodeSigningCert `
    -Subject 'CN=DFM Test Driver, O=DFM' `
    -CertStoreLocation Cert:\LocalMachine\My `
    -KeyAlgorithm RSA -KeyLength 2048 `
    -NotAfter (Get-Date).AddYears(5)
$thumb = $cert.Thumbprint
Write-Host "certificate: $thumb"

# 2) 用 MakeCat 生成 catalog（必须与 INF 同目录）
$cdf = @"
[CatalogHeader]
Name=winusb_dfm.cat
ResultDir=$pc
PublicVersion=0x00000001
CatalogVersion=2
HashAlgorithms=SHA256

[CatalogFiles]
<HASH>winusb_dfm.inf=winusb_dfm.inf
"@
$cdfPath = Join-Path $pc 'winusb_dfm.cdf'
Set-Content -Path $cdfPath -Value $cdf -Encoding ASCII
Push-Location $pc
& $mk $cdfPath
Pop-Location
if (-not (Test-Path $cat)) { throw 'makecat failed: winusb_dfm.cat was not created.' }
Write-Host "catalog: $cat"

# 3) 用 SignTool 签名 catalog
& $sig sign /v /sha1 $thumb /fd SHA256 $cat
if ($LASTEXITCODE -ne 0) { throw 'signtool failed.' }

# 4) 把测试证书装入“受信任的根证书颁发机构”和“受信任的发布者”
$root = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
$root.Open('ReadWrite')
$root.Add($cert)
$root.Close()
$tp = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'LocalMachine')
$tp.Open('ReadWrite')
$tp.Add($cert)
$tp.Close()
Write-Host 'certificate installed to Root + TrustedPublisher'

Write-Host ''
Write-Host 'DONE. Now install the driver (admin CMD):'
Write-Host "  pnputil /add-driver `"$inf`" /install"
Write-Host 'Or Device Manager -> right-click the "?" MI_03 node -> Update driver ->'
Write-Host '  Browse my computer -> Let me pick -> Have Disk -> select winusb_dfm.inf'
