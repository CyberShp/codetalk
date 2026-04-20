# CodeTalks K8s 内网部署指南

## 必须替换的信息清单

部署前用 `grep -rn "REPLACE\|HARBOR_HOST\|VERSION" k8s/` 确认所有占位符已替换。

### 1. 镜像地址（04-deployments.yaml）

| 占位符 | 替换为 | 示例 |
|--------|--------|------|
| `HARBOR_HOST` | 内网 Harbor 地址 | `harbor.yourcompany.com/codetalk` |
| `VERSION` | 镜像版本标签 | `v1.0.0` |

涉及的镜像共 7 个：

```
HARBOR_HOST/codetalk/postgres:VERSION      ← 基础镜像 postgres:16
HARBOR_HOST/codetalk/backend:VERSION       ← 本项目 backend/ 构建
HARBOR_HOST/codetalk/deepwiki:VERSION      ← 基础镜像 ghcr.io/asyncfuncai/deepwiki-open:latest
HARBOR_HOST/codetalk/gitnexus:VERSION      ← 本项目 docker/gitnexus/ 构建
HARBOR_HOST/codetalk/frontend:VERSION      ← 本项目 frontend/ 构建
HARBOR_HOST/codetalk/joern:VERSION         ← 基础镜像 ghcr.io/joernio/joern:nightly
HARBOR_HOST/codetalk/zoekt:VERSION         ← 基础镜像 ghcr.io/sourcegraph/zoekt:latest
```

### 2. 网络地址（02-configmap.yaml）

| 占位符 | 替换为 | 说明 |
|--------|--------|------|
| `REPLACE_WITH_NODE_IP` | K8s Node 的内网 IP | 用户浏览器能访问的地址 |

替换后示例：
```yaml
NEXT_PUBLIC_API_URL: "http://10.0.1.50:30800"
NEXT_PUBLIC_WS_URL: "ws://10.0.1.50:30800"
```

### 3. 密钥（03-secret.yaml）

| 字段 | 说明 |
|------|------|
| `POSTGRES_PASSWORD` | 数据库密码，base64 编码 |
| `FERNET_KEY` | 加密密钥，生成方式见文件注释 |
| `OPENAI_API_KEY` | LLM API Key（内网代理的 key，无则留空） |
| `ANTHROPIC_API_KEY` | 同上 |
| `GOOGLE_API_KEY` | 同上 |
| `DEEPWIKI_EMBEDDING_API_KEY` | Embedding 服务的 key |

### 4. 存储（01-pvc.yaml）

| PVC | 需要确认 |
|-----|---------|
| `code-volume` | 必须是 **ReadWriteMany**（NFS/CephFS），被多个 Pod 共享 |
| 其余 4 个 | ReadWriteOnce 即可 |

如果集群有指定的 StorageClass，取消注释 `storageClassName` 行：
```yaml
storageClassName: nfs-client    # 替换为实际的 StorageClass 名
```

---

## 部署步骤

### Step 1: 构建并推送镜像到 Harbor

```bash
# 设置变量
export HARBOR=harbor.yourcompany.com/codetalk
export VER=v1.0.0

# 基础镜像：直接 tag + push
docker pull postgres:16
docker tag postgres:16 $HARBOR/postgres:$VER
docker push $HARBOR/postgres:$VER

docker pull ghcr.io/joernio/joern:nightly
docker tag ghcr.io/joernio/joern:nightly $HARBOR/joern:$VER
docker push $HARBOR/joern:$VER

docker pull ghcr.io/sourcegraph/zoekt:latest
docker tag ghcr.io/sourcegraph/zoekt:latest $HARBOR/zoekt:$VER
docker push $HARBOR/zoekt:$VER

docker pull ghcr.io/asyncfuncai/deepwiki-open:latest
docker tag ghcr.io/asyncfuncai/deepwiki-open:latest $HARBOR/deepwiki:$VER
docker push $HARBOR/deepwiki:$VER

# 项目自建镜像：build + push
docker build -t $HARBOR/backend:$VER ./backend
docker push $HARBOR/backend:$VER

docker build -t $HARBOR/frontend:$VER ./frontend
docker push $HARBOR/frontend:$VER

docker build -t $HARBOR/gitnexus:$VER ./docker/gitnexus
docker push $HARBOR/gitnexus:$VER
```

### Step 2: 替换清单占位符

```bash
cd k8s/

# 镜像地址
sed -i 's|HARBOR_HOST/codetalk|harbor.yourcompany.com/codetalk|g' 04-deployments.yaml
sed -i 's|VERSION|v1.0.0|g' 04-deployments.yaml

# Node IP（用户浏览器访问后端的地址）
sed -i 's|REPLACE_WITH_NODE_IP|10.0.1.50|g' 02-configmap.yaml

# StorageClass（如需指定）
sed -i 's|# storageClassName:.*|storageClassName: nfs-client|g' 01-pvc.yaml
```

### Step 3: 填写 Secret

```bash
# 生成密码 base64
echo -n "your-strong-password" | base64
# 填入 03-secret.yaml 的 POSTGRES_PASSWORD

# 生成 Fernet key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 将输出 base64 编码后填入 FERNET_KEY
```

### Step 4: 部署到 K8s

```bash
# 按序 apply（namespace 和 PVC 先，Deployment 最后）
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-pvc.yaml
kubectl apply -f 02-configmap.yaml
kubectl apply -f 03-secret.yaml
kubectl apply -f 04-deployments.yaml
kubectl apply -f 05-services.yaml

# 或一次性
kubectl apply -f k8s/
```

### Step 5: 验证

```bash
# 查看 Pod 状态
kubectl -n codetalk get pods -w

# 期望输出（全部 Running/Ready）:
# postgres-0        1/1   Running
# backend-xxx       1/1   Running
# frontend-xxx      1/1   Running
# deepwiki-xxx      1/1   Running
# gitnexus-xxx      1/1   Running
# joern-xxx         1/1   Running   ← 启动较慢，30-60s
# zoekt-xxx         1/1   Running

# 测试 backend 健康
curl http://<node-ip>:30800/health
# {"status":"ok"}

# 测试工具连通
curl http://<node-ip>:30800/api/tools
# 4 个工具全部 healthy: true

# 访问前端
# 浏览器打开 http://<node-ip>:30003
```

---

## 资源占用（实际值，非上限）

| 服务 | 正常运行内存 | 峰值（分析大型 repo 时） |
|------|------|------|
| Joern | 2-4 GB | 6 GB |
| DeepWiki | 500 MB | 1.5 GB |
| PostgreSQL | 100-300 MB | 500 MB |
| Zoekt | 200-500 MB | 1 GB |
| GitNexus | 200-500 MB | 1 GB |
| Backend | 100-200 MB | 500 MB |
| Frontend | 80-150 MB | 200 MB |
| **合计** | **~4-6 GB** | **~11 GB** |

建议服务器配置：**16 GB RAM / 8 核 CPU** 即可平稳运行。

---

## 常见问题

### Q: Joern Pod 一直 CrashLoopBackOff？
A: 检查 node 可用内存。Joern JVM 需要 6G heap，如果 node 只剩 4G 空闲会 OOM-kill。

### Q: code-volume PVC 挂载失败？
A: 它要求 ReadWriteMany。确认 StorageClass 支持 RWX（NFS provisioner 等）。

### Q: frontend 显示"无法连接后端"？
A: `NEXT_PUBLIC_API_URL` 必须是**浏览器能访问的地址**（不是 K8s 内部 DNS）。
