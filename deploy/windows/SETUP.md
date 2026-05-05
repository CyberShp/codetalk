# Windows 端工具部署步骤

> 前提：Windows 已安装 Docker Desktop + WSL2 后端，Docker 数据目录在 SSD 上。

## 1. 准备目录结构

```powershell
# 在 SSD 上创建（例如 D:\codetalk-tools）
mkdir D:\codetalk-tools
cd D:\codetalk-tools
```

把 Mac 上 `deploy/windows/` 的全部内容拷到这个目录。最终结构：

```
D:\codetalk-tools\
  docker-compose.yml
  .env
  docker\
    codecompass\       ← 从 Mac 的 docker/codecompass/ 拷贝
      Dockerfile
      wrapper.py
      requirements.txt
    gitnexus\          ← 从 Mac 的 docker/gitnexus/ 拷贝
      Dockerfile
    deepwiki\          ← 从 Mac 的 docker/deepwiki/ 拷贝
      embedder.json
  repos\               ← 被分析的源码仓库（从 Mac 的 .repos/ 同步）
```

## 2. 拷贝构建文件

在 Mac 终端执行（或用文件共享拖拽）：

```bash
# 方式一：scp（需要 Windows 开启 SSH）
scp -r /Volumes/Media/codetalk/docker/codecompass user@192.168.50.195:D:/codetalk-tools/docker/
scp -r /Volumes/Media/codetalk/docker/gitnexus user@192.168.50.195:D:/codetalk-tools/docker/
scp -r /Volumes/Media/codetalk/docker/deepwiki user@192.168.50.195:D:/codetalk-tools/docker/

# 方式二：直接通过 SMB 共享文件夹拷贝
```

## 3. 同步源码仓库

```bash
# Mac 终端
scp -r /Volumes/Media/codetalk/.repos/* user@192.168.50.195:D:/codetalk-tools/repos/
```

## 4. Windows 防火墙放行（内网一条搞定）

以管理员身份运行 PowerShell：

```powershell
New-NetFirewallRule -DisplayName "CodeTalks LAN Allow All" -Direction Inbound -RemoteAddress 192.168.50.0/24 -Action Allow
```

这会允许 192.168.50.x 网段的所有入站流量，覆盖全部工具端口。

## 5. 启动

```powershell
cd D:\codetalk-tools
docker compose up -d
docker compose ps
```

等待所有服务 healthy（Joern 和 CodeCompass 启动较慢，约 30-60 秒）。

## 6. 验证连通

在 Mac 终端测试：

```bash
curl -sf http://192.168.50.195:8001/      && echo "deepwiki OK"
curl -sf http://192.168.50.195:7100/      && echo "gitnexus OK"
curl -sf http://192.168.50.195:6251/      && echo "codecompass OK"
curl -sf -X POST -H 'Content-Type: application/json' \
  -d '{"query":"version"}' http://192.168.50.195:8080/query-sync && echo "joern OK"
```

## 注意事项

- Windows 不要休眠/睡眠，否则工具全挂
- 建议给 Windows 设固定 IP（192.168.50.195），避免 DHCP 变动
- 每次在 Mac 上 clone 新的 repo 后，需要同步到 Windows 的 repos/ 目录
- Docker Desktop 资源分配建议：Memory >= 16GB, CPU >= 4 cores
