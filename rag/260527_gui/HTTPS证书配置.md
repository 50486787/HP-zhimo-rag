# HTTPS 证书配置（mkcert）

## 为什么

自签名证书浏览器每次弹"不安全"警告。mkcert 生成的是系统信任的本地 CA 签发的证书，浏览器不弹窗。

## 一次性操作（每台电脑只需做一次）

### 1. 安装 mkcert

```
winget install FiloSottile.mkcert
```

### 2. 安装本地 CA 根证书（需管理员权限）

管理员 PowerShell：

```
$mkcert = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\FiloSottile.mkcert_Microsoft.Winget.Source_8wekyb3d8bbwe\mkcert.exe"
& $mkcert -install
```

## 生成站点证书（每个项目目录执行一次）

```powershell
cd <项目目录>
$mkcert = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\FiloSottile.mkcert_Microsoft.Winget.Source_8wekyb3d8bbwe\mkcert.exe"
& $mkcert -key-file .cert\key.pem -cert-file .cert\cert.pem localhost 127.0.0.1 <主机名> <局域网IP>
```

示例：
```
& $mkcert -key-file .cert\key.pem -cert-file .cert\cert.pem localhost 127.0.0.1 Berial2 192.168.0.102
```

## 验证

Chrome/Edge 地址栏 → 锁图标 → 连接是安全的 → 证书有效。

## 员工客户端部署（只需一次，双击 bat）

1. 服务器导出 CA 证书：`mkcert -CAROOT` → 找到 `rootCA.pem`
2. 把 `rootCA.pem` + `安装证书.bat` 放到 NAS 共享目录
3. 员工打开共享，双击 `安装证书.bat` → 自动提权安装 → 完成

之后所有 mkcert 签发的站点证书自动受信，不再弹不安全。

## 过期时间

mkcert CA 约 10 年有效。站点证书 2 年 3 个月，过期后服务器重新签发即可，客户端无需重装 CA。
