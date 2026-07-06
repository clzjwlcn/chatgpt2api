# VPS 安装和 FlareSolverr 清障教程

本文适用于仓库：

```bash
https://github.com/clzjwlcn/chatgpt2api.git
```

## 1. 先说明截图里的报错

截图报错：

```text
Could not resolve proxy: privoxy
```

意思是：后台填写了 `http://privoxy:8118`，但是当前 Docker 网络里没有启动名为 `privoxy` 的容器。

解决办法不是只在后台打开开关，而是要用仓库里的清障 compose 启动：

```bash
docker compose -f docker-compose.warp.yml up -d --build
```

普通 `docker-compose.yml` 只启动主程序，不会启动 `warp-proxy`、`privoxy`、`flaresolverr`。

## 2. VPS 首次安装 Docker

Ubuntu / Debian 可以执行：

```bash
apt update
apt install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

检查：

```bash
docker version
docker compose version
git --version
```

## 3. 上传 / 克隆项目

推荐直接在 VPS 克隆：

```bash
cd /www/wwwroot/image2
git clone https://github.com/clzjwlcn/chatgpt2api.git
cd chatgpt2api
```

如果目录已存在：

```bash
cd /www/wwwroot/image2/chatgpt2api
git pull
```

## 4. 普通部署

不需要 WARP / FlareSolverr 时用这个：

```bash
docker compose down
docker compose up -d --build
```

访问：

```text
http://服务器IP:3000
```

## 5. 带 WARP / Privoxy / FlareSolverr 清障部署

需要后台清障功能时，用这个方式。

如果之前启动过普通部署，先停掉：

```bash
docker compose down
```

启动清障版：

```bash
docker compose -f docker-compose.warp.yml down
docker compose -f docker-compose.warp.yml up -d --build
```

查看容器：

```bash
docker compose -f docker-compose.warp.yml ps
```

应该能看到这些服务：

```text
chatgpt2api-warp
chatgpt2api-warp-proxy
chatgpt2api-privoxy
chatgpt2api-flaresolverr
```

访问：

```text
http://服务器IP:3000
```

## 6. 后台 FlareSolverr 页面怎么填

进入后台：

```text
设置 -> FlareSolverr
```

推荐填写：

| 项目 | 值 |
| --- | --- |
| 启用 FlareSolverr 清障 | 开启 |
| 出站模式 | 单代理/WARP |
| 清障代理 URL | `http://privoxy:8118` |
| 资源代理 URL | 留空 |
| 重置会话状态码 | `403` |
| 跳过 SSL 校验 | 可开启 |
| Clearance 模式 | `FlareSolverr` |
| FlareSolverr URL | `http://flaresolverr:8191` |
| 超时秒数 | `60` |
| 刷新间隔秒数 | `3600` |

然后点击：

```text
测试当前清障代理
测试 Clearance
```

两个都成功后，再去注册机或相关功能里重试。

## 7. 常用日志命令

主程序日志：

```bash
docker logs -f chatgpt2api-warp
```

FlareSolverr 日志：

```bash
docker logs -f chatgpt2api-flaresolverr
```

WARP 日志：

```bash
docker logs -f chatgpt2api-warp-proxy
```

Privoxy 日志：

```bash
docker logs -f chatgpt2api-privoxy
```

## 8. 升级代码

普通部署升级：

```bash
cd /www/wwwroot/image2/chatgpt2api
git pull
docker compose down
docker compose up -d --build
```

清障版部署升级：

```bash
cd /www/wwwroot/image2/chatgpt2api
git pull
docker compose -f docker-compose.warp.yml down
docker compose -f docker-compose.warp.yml up -d --build
```

## 9. 数据备份

升级或重装前建议备份：

```bash
cd /www/wwwroot/image2/chatgpt2api
tar -czf chatgpt2api-backup-$(date +%F-%H%M%S).tar.gz config.json data .env 2>/dev/null || true
```

重要数据：

| 路径 | 内容 |
| --- | --- |
| `config.json` | 后台配置、网站设置、代理设置 |
| `data/` | 账号、用户 key、日志、图片任务等数据 |
| `.env` | Docker 环境变量 |

## 10. 常见问题

### 1. 后台测试代理报 `Could not resolve proxy: privoxy`

说明没有用清障版 compose 启动，执行：

```bash
docker compose down
docker compose -f docker-compose.warp.yml up -d --build
```

### 2. 端口打不开

检查安全组和防火墙是否放行 `3000`：

```bash
docker compose -f docker-compose.warp.yml ps
ss -lntp | grep 3000
```

### 3. 修改后台设置后仍然失败

重启清障版：

```bash
docker compose -f docker-compose.warp.yml restart
```

再看日志：

```bash
docker logs -f chatgpt2api-warp
docker logs -f chatgpt2api-flaresolverr
```
