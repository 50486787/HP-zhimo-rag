# 局域网 HTTPS 证书方案

## 问题

几十台员工电脑通过局域网访问 Web 搜索服务（HTTPS），自签名证书每台浏览器弹"不安全"警告，不能手动逐台安装。

## 方案：mkcert + 一键 bat

### 原理

mkcert 生成本地 CA 根证书，用这个 CA 签发站点证书。客户端只需信任这个 CA 一次，之后该 CA 签发的所有证书都自动受信。

### 服务器（一次性）

1. 安装 mkcert：`winget install FiloSottile.mkcert`
2. 安装 CA：`mkcert -install`
3. 签发站点证书：`mkcert -key-file .cert\key.pem -cert-file .cert\cert.pem localhost 127.0.0.1 <主机名> <IP>`
4. 找到 CA 证书：`mkcert -CAROOT` → `rootCA.pem`
5. 把 `rootCA.pem` + `安装证书.bat` 放到 NAS 共享目录

### 员工（每人一次，双击 bat）

```
\\NAS\共享\安装证书.bat
```

自动提权 → certutil 安装 CA → 完成。之后浏览器访问 Web 搜索服务不再弹不安全。

### 过期

mkcert CA 有效期约 10 年。站点证书 2 年 3 个月，过期后服务器重新签发即可，客户端无需重新安装 CA。

## 文件清单

- `rootCA.pem` — mkcert 导出，放 NAS 共享
- `安装证书.bat` — 员工双击执行，和 rootCA.pem 同目录
