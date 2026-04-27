# CodeTalks 内网 K8s 部署手册

> 适用场景：公司内网 air-gapped 环境，K8s 集群 + Harbor 镜像仓库，无法直连 Docker Hub / ghcr.io / npm / GitHub。

---

## 目录

1. [Pre-flight 决策门（必读）](#1-pre-flight-决策门必读)
2. [架构概述与关键约束](#2-架构概述与关键约束)
3. [源码入库方案选择](#3-源码入库方案选择)
4. [Phase 1 — 外网镜像准备](#4-phase-1--外网镜像准备)
5. [Phase 2 — 镜像导入内网](#5-phase-2--镜像导入内网)
6. [Phase 3 — K8s 部署](#6-phase-3--k8s-部署)
7. [Phase 4 — 部署验证](#7-phase-4--部署验证)
8. [FAQ / Troubleshooting](#8-faq--troubleshooting)

---

## 1. Pre-flight 决策门（必读）

**在执行任何操作之前，必须拍板以下 4 个问题。** 不解决这 4 个问题，Phase 3 的 K8s YAML 会反复返工。

| # | 问题 | 影响 | 待确认 |
|---|------|------|--------|
| Q1 | 集群是否有支持 **ReadWriteMany** 的 StorageClass？（NFS、CephFS、Longhorn RWX 等） | backend / deepwiki / gitnexus 三个 Pod 必须共享同一个 `/data/repos` 卷，没有 RWX 则无法直接部署 | ☐ 有 ☐ 无 |
| Q2 | 内网是否有 **Git 镜像服务**？（GitLab/Gitea 等可访问的内网 Git 实例） | 决定"源码入库"走哪条路；无内网 Git 则必须走"预置共享卷"方案 | ☐ 有 ☐ 无 |
| Q3 | 内网是否有 **LLM / Embedding 兼容端点**？（OpenAI 格式的内网代理、Ollama 等） | deepwiki 的 AI 问答和 embedding 功能依赖此端点；无则 AI 功能不可用，但代码搜索和 GitNexus 仍可正常工作 | ☐ 有 ☐ 无 |
| Q4 | ~~本次部署是否包含前端（Next.js）？~~ | **已确认：包含。** 前端镜像、Deployment、Service 已在本文档覆盖 | ☑ 包含 |

---

## 2. 架构概述与关键约束

### 2.1 服务拓扑

```
内网用户浏览器
      │
      ▼ (NodePort :30800)
  frontend:3000 (NodePort :30003)
      │
      ▼ (NEXT_PUBLIC_API_URL)
  backend:8000  (NodePort :30800) ── postgres:5432
      │
      ├── deepwiki:8001  (HTTP, ClusterIP)
      ├── gitnexus:7100  (HTTP, ClusterIP)
      ├── zoekt:6070     (HTTP, ClusterIP)
      ├── joern:8080     (HTTP, ClusterIP)
      └── codecompass:6251 (待集成，本文档暂不覆盖)

  /data/repos  ← RWX PVC，五个 Pod 共享
      ├── backend     (ReadWrite — 写入 clone/upload 的源码)
      ├── deepwiki    (ReadOnly  — 读取源码建索引)
      ├── gitnexus    (ReadWrite — 读取源码构建知识图谱)
      ├── zoekt       (ReadOnly  — 读取源码建搜索索引)
      └── joern       (ReadOnly  — 读取源码构建 CPG)
```

### 2.2 关键设计约束（来自代码审查）

| 约束 | 来源 | K8s 处置方式 |
|------|------|-------------|
| `/data/repos` 必须是 **RWX 共享卷** | `source_manager.py:50`、`deepwiki.py:89`、`gitnexus.py:75`、`zoekt`、`joern` — 五个服务通过绝对路径共享源码 | 使用 RWX PVC；无 RWX 则参考 [FAQ 1](#81-集群没有-readwritemany-storageclass-怎么办) |
| **docker.sock 挂载必须删除** | `component_manager.py:311` 有真实 Docker Engine API 调用（Unix socket），写 `override.yml` 并重启容器 | K8s 中无此语义；对应的「在线改配置并重启组件」UI 功能须明确告知用户**在 K8s 版中不可用** |
| **docker-compose.override.yml 语义消失** | `component_manager.py:292` 写 override 文件，依赖 `/project` bind mount | 删除 `/project` 挂载；配置变更改为修改 ConfigMap/Secret + `kubectl rollout restart` |
| **`git_url` 源类型需要内网 Git** | `source_manager.py:68` — 运行时执行 `git clone/pull` | Q2 为"有"则直接用内网 Git URL；Q2 为"无"则只能走预置共享卷 + `local_path` |
| **`zip_upload` 目前不是可用功能** | `source_manager.py:40` 只检查 `repo.local_path` 是否存在、`assets/page.tsx:464` UI 入口已禁用、API 无文件上传接口 | 不要引导用户使用此选项；实际可用选项只有 `git_url` 和 `local_path` |
| **backend 镜像去掉 `--reload`** | `backend/Dockerfile:12` CMD 带 `--reload`（开发模式）| 生产镜像去掉 `--reload`；Alembic 迁移抽成独立 Job |
| **postgres 用 StatefulSet** | PostgreSQL 有序重启语义、稳定 Pod 标识 | 用 `StatefulSet` + `volumeClaimTemplates` 而不是 `Deployment` |

---

## 3. 源码入库方案选择

> 这是内网落地的核心问题。**用户需要先把代码放进 `/data/repos` 共享卷，才能触发分析任务。** 请根据 Q1/Q2 的答案选择方案。

### 方案 A：内网 Git 镜像（推荐，Q2=有）

前提：公司内有 GitLab/Gitea 等内网 Git 服务，待分析仓库已镜像/导入其中。

操作流程：
1. 将待分析仓库上传/镜像到内网 Git（如 `http://gitlab.company.com/team/myrepo.git`）
2. 在 CodeTalks UI 中添加仓库时，Source Type 选择 **git_url**，填写内网 Git 地址
3. backend Pod 会执行 `git clone --depth=1`，源码落地到共享卷

```
内网 Git (gitlab.company.com)
        ↑ 镜像/上传（人工操作）
        |
backend Pod  --git clone-->  /data/repos/<repo-uuid>/
                                   ↑ 共享 RWX PVC
                             deepwiki, gitnexus 直接读取
```

### 方案 B：预置共享卷（Q2=无 或 仓库无法迁移到内网 Git）

前提：通过其他合规手段将源码拷贝到 K8s 节点，再写入共享卷。

操作流程：
1. 将源码 zip 包通过合规通道传入内网
2. 在能访问 K8s 的机器上，通过临时 Pod 将源码写入共享卷：
   ```bash
   # 创建一个临时 Pod，挂载共享卷，上传源码
   kubectl run upload-helper \
     --image=harbor.company.com/codetalk/backend:v1.0.0 \
     --restart=Never \
     -n codetalk \
     --overrides='{"spec":{"volumes":[{"name":"code","persistentVolumeClaim":{"claimName":"code-volume"}}],"containers":[{"name":"upload-helper","image":"harbor.company.com/codetalk/backend:v1.0.0","command":["sleep","3600"],"volumeMounts":[{"name":"code","mountPath":"/data/repos"}]}]}}'

   # 将源码复制进去（REPO_UUID 需与数据库中的仓库 UUID 一致）
   REPO_UUID="<从 CodeTalks UI 获取>"
   kubectl cp myrepo/ codetalk/upload-helper:/data/repos/$REPO_UUID/

   # 完成后删除临时 Pod
   kubectl delete pod upload-helper -n codetalk
   ```
3. 在 CodeTalks 中，Source Type 选择 **local_path**，路径填 `/data/repos/<REPO_UUID>`

> **注意**：`local_path` 必须在 `settings.repos_base_path`（即 `/data/repos`）目录下，否则 `source_manager.py:29` 的边界检查会拒绝访问。

### 方案对比

| | 方案 A（内网 Git） | 方案 B（预置共享卷） |
|---|---|---|
| 操作复杂度 | 低，UI 直接填 URL | 高，需要 kubectl 操作 |
| 可重复性 | 高，支持 `git pull` 更新 | 低，每次更新需重新复制 |
| 适用场景 | 公司有 GitLab/Gitea | 无内网 Git，或代码不能上传到任何服务 |

---

## 4. Phase 1 — 外网镜像准备

> **在外网准备机上执行**，需要能访问 Docker Hub、ghcr.io、npmjs.org、deb.debian.org。

### 4.1 前置要求

| 要求 | 说明 |
|------|------|
| Docker ≥ 20.10 | 能访问外网所有 registry |
| 磁盘空间 ≥ 30 GB | 7 个镜像 tar 文件合计约 8–15 GB（Joern 含 JVM 约 1.5 GB） |
| 平台 | 建议 linux/amd64；Mac 执行时脚本已加 `--platform linux/amd64` |

> **特别说明**：PC 安全机不能执行 Docker 命令，也不能访问本地文件系统，**不要在 PC 安全机上执行本节操作**。外网构建必须在有 Docker 权限的专用机器上进行。

### 4.2 修改 backend Dockerfile（移除开发模式）

**在构建镜像前**，先修改 `backend/Dockerfile`，去掉 `--reload` 标志：

```diff
- CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
+ CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

> 原因：`--reload` 会监听文件系统变化重启进程，是开发模式专用标志，生产容器不应使用。K8s 已移除源码 bind mount，`--reload` 无实际意义且浪费资源。

### 4.3 固定 gitnexus 版本（提高可复现性）

编辑 `docker/gitnexus/Dockerfile`，将 `@latest` 改为固定版本：

```diff
- RUN npm install -g gitnexus@latest
+ RUN npm install -g gitnexus@0.4.2   # 替换为当前最新稳定版
```

查询当前最新版本：`npm view gitnexus version`

> 原因：`@latest` 在不同时间构建会得到不同版本，内网无法复现。固定版本后，tar 文件内容可预期。

### 4.4 执行构建脚本

```bash
git clone <codetalk-repo-url>
cd codetalk

chmod +x scripts/build-images.sh
VERSION=v1.0.0 bash scripts/build-images.sh
```

脚本依次：
1. 拉取 `postgres:16` → `postgres-v1.0.0.tar`
2. 构建 backend（含 pip install 12 个依赖） → `backend-v1.0.0.tar`
3. 拉取 `ghcr.io/asyncfuncai/deepwiki-open:latest` → `deepwiki-v1.0.0.tar`
4. 构建 gitnexus（node:20-slim + Debian Trixie libstdc++ + npm gitnexus） → `gitnexus-v1.0.0.tar`
5. 构建 frontend（Next.js standalone，含运行时 URL 替换） → `frontend-v1.0.0.tar`
6. 拉取 `ghcr.io/joernio/joern:nightly`（JVM，约 1.5 GB） → `joern-v1.0.0.tar`
7. 拉取 `ghcr.io/sourcegraph/zoekt:latest` → `zoekt-v1.0.0.tar`

完成后验证：

```bash
ls -lh image-export/
# postgres-v1.0.0.tar   ~400MB
# backend-v1.0.0.tar    ~500MB
# deepwiki-v1.0.0.tar   ~2GB
# gitnexus-v1.0.0.tar   ~800MB
# frontend-v1.0.0.tar   ~400MB
# joern-v1.0.0.tar      ~1.5GB
# zoekt-v1.0.0.tar      ~200MB
```

> **为什么 gitnexus 不能在内网构建？** `docker/gitnexus/Dockerfile` 依赖：① `deb.debian.org/debian trixie` 安装 libstdc++（GLIBCXX_3.4.31），② `registry.npmjs.org` 安装 gitnexus 包。内网均无法访问，必须在外网预构建成最终镜像。

---

## 5. Phase 2 — 镜像导入内网

> **在内网 Docker 操作机上执行**（不是 PC 安全机）。

### 5.1 传输 tar 文件

> **传输方式**：将 tar 文件上传到 CSDN/博客园等可下载的网站，内网 PC 安全机通过浏览器下载，再通过内网传输到 Docker 操作机。

由于博客网站通常限制单文件 200-500MB，大镜像需要先分片：

```bash
# 外网：分片（每片 200MB）
for tar in image-export/*.tar; do
  split -b 200m "$tar" "${tar}.part."
done

# 内网下载后：合并
cat deepwiki-v1.0.0.tar.part.* > deepwiki-v1.0.0.tar
cat gitnexus-v1.0.0.tar.part.* > gitnexus-v1.0.0.tar
# 其他较小的 tar 可能不需要分片
```

### 5.2 登录 Harbor 并导入

```bash
docker login harbor.company.com

chmod +x scripts/import-images.sh
IMPORT_DIR=./image-export \
  bash scripts/import-images.sh harbor.company.com v1.0.0
```

### 5.3 验证 Harbor

浏览器打开 `https://harbor.company.com`，确认 `codetalk` 项目下有 7 个镜像：
- `codetalk/postgres:v1.0.0`
- `codetalk/backend:v1.0.0`
- `codetalk/deepwiki:v1.0.0`
- `codetalk/gitnexus:v1.0.0`
- `codetalk/frontend:v1.0.0`
- `codetalk/joern:v1.0.0`
- `codetalk/zoekt:v1.0.0`

---

## 6. Phase 3 — K8s 部署

> **在能执行 kubectl 的机器上执行**（跳板机或 K8s 管理节点）。

### 6.1 传输 K8s 清单

将项目 `k8s/` 目录与 image-export 同批次传入内网。

### 6.2 替换占位符

```bash
cd k8s/
HARBOR_HOST="harbor.company.com"
VERSION="v1.0.0"

for f in *.yaml; do
  sed -i "s/HARBOR_HOST/$HARBOR_HOST/g; s/:VERSION/:$VERSION/g" "$f"
done
```

### 6.3 填写 Secret

编辑 `03-secret.yaml`，用 base64 编码替换占位符：

```bash
# POSTGRES_PASSWORD
echo -n "StrongPass123" | base64

# FERNET_KEY（加密存储的 API Key 用的密钥）
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | base64

# 内网 LLM 代理的 API Key（若无则留空: echo -n "" | base64 = ""）
echo -n "your-api-key" | base64
```

> **安全提示**：`03-secret.yaml` 不要提交到 Git。

### 6.4 确认 StorageClass（RWX 必须）

```bash
kubectl get storageclass
# 找到 PROVISIONER 列包含 nfs / ceph / longhorn 的 StorageClass
# 确认 code-volume 的 accessModes 是 ReadWriteMany

# 编辑 01-pvc.yaml，取消 storageClassName 注释并填写
```

若没有 RWX StorageClass，先解决 [FAQ 1](#81-集群没有-readwritemany-storageclass-怎么办) 再继续。

### 6.5 填写前端 URL（必做）

编辑 `02-configmap.yaml`，将 `REPLACE_WITH_NODE_IP` 替换为 K8s 节点的实际内网 IP：

```bash
# 查看节点 IP
kubectl get nodes -o wide
# 找 INTERNAL-IP 列

NODE_IP="10.x.x.x"  # 替换为实际 IP
sed -i "s/REPLACE_WITH_NODE_IP/$NODE_IP/g" 02-configmap.yaml
```

> **原因**：前端是浏览器端应用，用户浏览器需要直接访问后端 API。`NEXT_PUBLIC_API_URL` 和 `NEXT_PUBLIC_WS_URL` 必须是**用户浏览器能访问到的地址**，不是集群内部 DNS。

### 6.6 按序 Apply

```bash
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-pvc.yaml
kubectl apply -f 02-configmap.yaml
kubectl apply -f 03-secret.yaml
kubectl apply -f 04-deployments.yaml
kubectl apply -f 05-services.yaml
```

### 6.7 等待 Pod 就绪

```bash
kubectl get pods -n codetalk -w
# 预期：postgres → ~15s，backend → ~30s，gitnexus → ~30s，deepwiki → ~60s，zoekt → ~10s，joern → ~60s（JVM 启动较慢）
```

### 6.8 执行数据库迁移

```bash
BACKEND_POD=$(kubectl get pod -n codetalk -l app=backend \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n codetalk $BACKEND_POD -- alembic upgrade head
```

### 6.9 重要说明：「在线修改组件配置」功能在 K8s 中不可用

CodeTalks 的「组件配置」UI（`/components` 路由）在 docker-compose 模式下通过 Docker Engine API 写 `docker-compose.override.yml` 并重启容器（`component_manager.py:292,311`）。**K8s 环境中此功能无效，点击"应用配置"不会生效。**

K8s 下修改组件配置的正确方式：
```bash
# 1. 修改 ConfigMap 或 Secret
kubectl edit configmap codetalk-config -n codetalk
# 或
kubectl edit secret codetalk-secret -n codetalk

# 2. 滚动重启受影响的 Deployment
kubectl rollout restart deployment/deepwiki -n codetalk
kubectl rollout restart deployment/backend -n codetalk
kubectl rollout restart deployment/joern -n codetalk
kubectl rollout restart deployment/zoekt -n codetalk
```

---

## 7. Phase 4 — 部署验证

### 7.1 健康检查

```bash
NODE_IP=$(kubectl get nodes \
  -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

# backend API
curl http://$NODE_IP:30800/health

# 集群内 deepwiki 连通性（通过 backend Pod 测）
BACKEND_POD=$(kubectl get pod -n codetalk -l app=backend \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n codetalk $BACKEND_POD -- curl -s http://deepwiki:8001/api/health
kubectl exec -n codetalk $BACKEND_POD -- curl -s http://gitnexus:7100/health
kubectl exec -n codetalk $BACKEND_POD -- curl -s http://zoekt:6070/healthz
kubectl exec -n codetalk $BACKEND_POD -- \
  curl -sf -X POST -H 'Content-Type: application/json' \
  -d '{"query":"version"}' http://joern:8080/query-sync
```

### 7.2 查看日志

```bash
kubectl logs -n codetalk -l app=backend  --tail=100 -f
kubectl logs -n codetalk -l app=deepwiki --tail=100 -f
kubectl logs -n codetalk -l app=gitnexus --tail=100 -f
kubectl logs -n codetalk -l app=zoekt    --tail=100 -f
kubectl logs -n codetalk -l app=joern    --tail=100 -f
```

### 7.3 功能冒烟测试

1. **前端访问**：浏览器打开 `http://<node-ip>:30003`，确认页面正常加载
2. **API 连通**：在前端页面打开浏览器 DevTools → Network，确认对 `<node-ip>:30800` 的请求成功（非 CORS 错误）
3. **源码入库**：按第 3 节选择的方案，将一个测试仓库导入系统
4. **触发分析任务**：通过 UI 选择 gitnexus + deepwiki + joern 工具
5. **确认任务完成**：`kubectl exec $BACKEND_POD -- curl -s http://localhost:8000/api/tasks/<task-id>`

---

## 8. FAQ / Troubleshooting

### 8.1 集群没有 ReadWriteMany StorageClass 怎么办？

**方案 A（推荐）**：用 NFS subdir provisioner 快速搭建 RWX StorageClass：
```bash
helm repo add nfs-subdir https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
helm install nfs-provisioner nfs-subdir/nfs-subdir-external-provisioner \
  --set nfs.server=<nfs-node-ip> \
  --set nfs.path=/exports/codetalk \
  --set storageClass.name=nfs-rwx \
  -n codetalk
```
然后在 `01-pvc.yaml` 的 `code-volume` 中填 `storageClassName: nfs-rwx`。

**方案 B（单节点 workaround）**：如果集群只有 1 个工作节点，可以把 `code-volume` 改为 `ReadWriteOnce`，并给 backend / deepwiki / gitnexus / zoekt / joern 五个 Deployment 加相同的 `nodeSelector`，强制调度到同一节点：
```yaml
nodeSelector:
  kubernetes.io/hostname: <single-node-name>
```

---

### 8.2 Pod 启动失败：ImagePullBackOff

```bash
kubectl describe pod -n codetalk <pod-name> | grep -A10 Events
```

常见原因：
1. HARBOR_HOST 占位符未替换 → 重新执行 6.2 的 sed 命令
2. 未配置 imagePullSecret：
   ```bash
   kubectl create secret docker-registry harbor-cred \
     --docker-server=harbor.company.com \
     --docker-username=<user> \
     --docker-password=<pass> \
     -n codetalk
   ```
   再在 `04-deployments.yaml` 每个 Deployment 的 `spec.template.spec` 中添加：
   ```yaml
   imagePullSecrets:
     - name: harbor-cred
   ```

---

### 8.3 backend 启动报 alembic 错误

```bash
BACKEND_POD=$(kubectl get pod -n codetalk -l app=backend \
  -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n codetalk $BACKEND_POD -- alembic upgrade head
```

---

### 8.4 deepwiki embedding 报错 / AI 功能不可用

确认 Q3 的答案：

**有内网 LLM 代理**：
```bash
# 在 02-configmap.yaml 中添加
OPENAI_BASE_URL: "http://internal-llm.company.com/v1"
DEEPWIKI_EMBEDDING_BASE_URL: "http://internal-llm.company.com/v1"
# 在 03-secret.yaml 中填写对应 API Key
# 然后重启
kubectl rollout restart deployment/deepwiki -n codetalk
```

**有 Ollama（内网本地模型）**：
```yaml
# 02-configmap.yaml
DEEPWIKI_EMBEDDER_TYPE: "ollama"
OLLAMA_BASE_URL: "http://ollama.<namespace>:11434"
```
同时将 `deepwiki-embedder-config` ConfigMap 中 `embedder.json` 的 `embedder.client_class` 改为 `OllamaClient`。

**无任何 LLM 端点**：deepwiki 的 AI 问答和 embedding 功能不可用，但 gitnexus 知识图谱、代码搜索功能仍然可以正常使用。

---

### 8.5 「应用组件配置」按钮点了没反应

K8s 部署下，这是预期行为。原因：此功能依赖 Docker Engine API（Unix socket）和 `docker-compose.override.yml` 文件，在 K8s 中均不存在。

正确的配置更新方式见 [Phase 3 第 6.9 节](#69-重要说明在线修改组件配置功能在-k8s-中不可用)。

---

### 8.6 如何更新镜像版本

```bash
# 外网重新构建
VERSION=v1.1.0 bash scripts/build-images.sh

# 内网导入
bash scripts/import-images.sh harbor.company.com v1.1.0

# 滚动更新（不停服）
kubectl set image deployment/backend \
  backend=harbor.company.com/codetalk/backend:v1.1.0 -n codetalk
kubectl set image deployment/deepwiki \
  deepwiki=harbor.company.com/codetalk/deepwiki:v1.1.0 -n codetalk
kubectl set image deployment/frontend \
  frontend=harbor.company.com/codetalk/frontend:v1.1.0 -n codetalk
kubectl set image deployment/joern \
  joern=harbor.company.com/codetalk/joern:v1.1.0 -n codetalk
kubectl set image deployment/zoekt \
  zoekt=harbor.company.com/codetalk/zoekt:v1.1.0 -n codetalk
# postgres StatefulSet 更新：
kubectl set image statefulset/postgres \
  postgres=harbor.company.com/codetalk/postgres:v1.1.0 -n codetalk
```

---

### 8.7 如何访问前端

```bash
# 浏览器打开
http://<node-ip>:30003
```

如果页面加载但 API 请求失败，检查 ConfigMap 中的 `NEXT_PUBLIC_API_URL` 是否正确指向 backend NodePort，然后重启 frontend：

```bash
kubectl rollout restart deployment/frontend -n codetalk
```

> 原因：frontend 的 entrypoint 在容器启动时将 ConfigMap 中的 URL 注入到 Next.js 的编译产物中。修改 ConfigMap 后必须重启 Pod 才会生效。

---

*文档版本：v1.3 | 2026-04-27 | 布偶猫/宪宪 整理，新增 Joern + Zoekt 内网部署全覆盖*
