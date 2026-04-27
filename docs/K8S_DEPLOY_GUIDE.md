# CodeTalks K8s 部署手册（内网版）

> 版本 1.0 · 2026-04-20
>
> 本手册从零开始，一步一步将 CodeTalks 部署到内网 K8s 集群。
> 按顺序执行，不跳步骤，不需要做判断，遇到分支会明确告诉你选哪条。

---

## 目录

- [第一章 你需要准备什么](#第一章-你需要准备什么)
- [第二章 在外网机器上构建镜像](#第二章-在外网机器上构建镜像)
- [第三章 把镜像搬进内网](#第三章-把镜像搬进内网)
- [第四章 部署到 K8s](#第四章-部署到-k8s)
- [第五章 验证部署是否成功](#第五章-验证部署是否成功)
- [第六章 怎么把代码导入系统](#第六章-怎么把代码导入系统)
- [第七章 日常运维操作](#第七章-日常运维操作)
- [第八章 出了问题怎么办](#第八章-出了问题怎么办)

---

## 第一章 你需要准备什么

开始之前，先确认以下条件。**全部满足才能继续**，哪个不满足就先解决哪个。

### 1.1 需要两台机器

| 机器 | 要求 | 用途 |
|------|------|------|
| **外网机** | 能上网、装了 Docker、20GB 空闲磁盘 | 下载依赖、构建镜像 |
| **内网机** | 能执行 `kubectl` 命令、能访问 Harbor | 导入镜像、部署 K8s |

> 这两台不需要是同一台。外网机只在构建镜像时使用一次。

### 1.2 内网基础设施清单

逐条确认，打勾：

```
[ ] K8s 集群正常运行（kubectl get nodes 能看到 Ready 的节点）
[ ] Harbor 镜像仓库可用（浏览器能打开 Harbor 页面）
[ ] Harbor 上已创建名为 "codetalk" 的项目（没有就手动建一个）
```

### 1.3 一个关键问题：你的集群支持 RWX 存储吗？

**什么是 RWX？** 就是一块存储能被多个 Pod 同时读写。CodeTalks 有 3 个服务需要共同访问同一个代码目录。

**怎么查？** 在内网机上执行：

```bash
kubectl get storageclass
```

看输出中有没有 NFS、CephFS、Longhorn 类型的 StorageClass。如果有，记下它的名字（后面要填到配置文件里）。

**如果没有 RWX 存储**，有两个解决办法（选一个）：

<details>
<summary>办法 A：装一个 NFS 存储（推荐，5 分钟搞定）</summary>

需要一台内网机器有空闲磁盘，作为 NFS 服务端。

**在 NFS 服务端机器上**：
```bash
# 安装 NFS 服务
yum install -y nfs-utils   # CentOS/RHEL
# 或
apt install -y nfs-kernel-server   # Ubuntu/Debian

# 创建共享目录
mkdir -p /exports/codetalk
chmod 777 /exports/codetalk

# 配置导出（替换 10.0.0.0/24 为你的 K8s 节点网段）
echo "/exports/codetalk 10.0.0.0/24(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
exportfs -ra

# 启动服务
systemctl enable --now nfs-server
```

**在 K8s 管理节点上**：
```bash
# 安装 NFS StorageClass provisioner
helm repo add nfs-subdir https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
helm install nfs-provisioner nfs-subdir/nfs-subdir-external-provisioner \
  --set nfs.server=<NFS服务端IP> \
  --set nfs.path=/exports/codetalk \
  --set storageClass.name=nfs-rwx \
  -n kube-system
```

装好后，你的 RWX StorageClass 名字就是 `nfs-rwx`，后面要用到。

</details>

<details>
<summary>办法 B：单节点 workaround（集群只有 1 个工作节点时可用）</summary>

如果你的 K8s 集群只有一个工作节点，不需要 RWX。后面部署时会告诉你改一行配置。

记住这件事，到第四章会提到。

</details>

### 1.4 CodeTalks 源代码

获取 CodeTalks 项目的源代码，确认目录里有：

```
codetalk/
  ├── k8s/           ← 6 个 YAML 文件
  ├── backend/       ← 后端代码和 Dockerfile
  ├── frontend/      ← 前端代码和 Dockerfile
  ├── docker/        ← gitnexus 和 semgrep 的 Dockerfile
  └── docker-compose.yml
```

---

## 第二章 在外网机器上构建镜像

> **在哪执行**：外网机（能上网的那台）
>
> **目标**：产出 5 个 `.tar` 镜像文件

### 第 1 步：修改两个文件

在构建之前，先做两处修改，否则部署到生产环境会出问题。

**修改 1**：打开 `backend/Dockerfile`，找到最后一行 CMD，把 `--reload` 去掉：

```
改前：CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
改后：CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

> 为什么：`--reload` 是开发模式，生产不需要，去掉减少资源浪费。

**修改 2**：打开 `docker/gitnexus/Dockerfile`，把 `@latest` 改成具体版本号：

```
改前：RUN npm install -g gitnexus@latest
改后：RUN npm install -g gitnexus@0.4.2
```

> 怎么查最新版本号：在外网机执行 `npm view gitnexus version`

### 第 2 步：构建 5 个镜像

依次执行以下命令（每个大约 2-10 分钟）：

```bash
cd codetalk

# 镜像 1：PostgreSQL 数据库（直接拉取官方镜像）
docker pull --platform linux/amd64 postgres:16
docker save postgres:16 -o postgres.tar

# 镜像 2：后端 API 服务
docker build --platform linux/amd64 -t codetalk/backend:v1.0.0 ./backend
docker save codetalk/backend:v1.0.0 -o backend.tar

# 镜像 3：DeepWiki 文档引擎（直接拉取开源镜像）
docker pull --platform linux/amd64 ghcr.io/asyncfuncai/deepwiki-open:latest
docker tag ghcr.io/asyncfuncai/deepwiki-open:latest codetalk/deepwiki:v1.0.0
docker save codetalk/deepwiki:v1.0.0 -o deepwiki.tar

# 镜像 4：GitNexus 知识图谱引擎
docker build --platform linux/amd64 -t codetalk/gitnexus:v1.0.0 ./docker/gitnexus
docker save codetalk/gitnexus:v1.0.0 -o gitnexus.tar

# 镜像 5：前端 Web 界面
docker build --platform linux/amd64 -t codetalk/frontend:v1.0.0 ./frontend
docker save codetalk/frontend:v1.0.0 -o frontend.tar
```

### 第 3 步：确认产出

```bash
ls -lh *.tar
```

你应该看到 5 个文件：

| 文件 | 大约大小 |
|------|---------|
| postgres.tar | 400 MB |
| backend.tar | 500 MB |
| deepwiki.tar | 2 GB |
| gitnexus.tar | 800 MB |
| frontend.tar | 400 MB |

**外网机的工作到此结束。** 下一步是把这 5 个 tar 文件搬进内网。

---

## 第三章 把镜像搬进内网

> **在哪执行**：内网机（能执行 kubectl 和 docker 的那台）
>
> **目标**：5 个镜像全部推送到 Harbor

### 第 1 步：把 tar 文件传入内网

用你们公司的内网文件传输方式，把 5 个 `.tar` 文件传到内网机上。

> 如果文件太大传不了，先在外网机上切割：
> ```bash
> split -b 200m deepwiki.tar deepwiki.tar.part.
> ```
> 内网拿到后合并：
> ```bash
> cat deepwiki.tar.part.* > deepwiki.tar
> ```

### 第 2 步：导入镜像到本地 Docker

```bash
docker load -i postgres.tar
docker load -i backend.tar
docker load -i deepwiki.tar
docker load -i gitnexus.tar
docker load -i frontend.tar
```

每个命令执行后会输出一行 `Loaded image: xxx`，确认 5 个都成功。

### 第 3 步：给镜像打 Harbor 标签

把下面的 `harbor.company.com` 换成你们实际的 Harbor 地址：

```bash
HARBOR=harbor.company.com

docker tag postgres:16                   $HARBOR/codetalk/postgres:v1.0.0
docker tag codetalk/backend:v1.0.0       $HARBOR/codetalk/backend:v1.0.0
docker tag codetalk/deepwiki:v1.0.0      $HARBOR/codetalk/deepwiki:v1.0.0
docker tag codetalk/gitnexus:v1.0.0      $HARBOR/codetalk/gitnexus:v1.0.0
docker tag codetalk/frontend:v1.0.0      $HARBOR/codetalk/frontend:v1.0.0
```

### 第 4 步：推送到 Harbor

```bash
docker login $HARBOR
# 输入 Harbor 用户名和密码

docker push $HARBOR/codetalk/postgres:v1.0.0
docker push $HARBOR/codetalk/backend:v1.0.0
docker push $HARBOR/codetalk/deepwiki:v1.0.0
docker push $HARBOR/codetalk/gitnexus:v1.0.0
docker push $HARBOR/codetalk/frontend:v1.0.0
```

### 第 5 步：验证 Harbor

打开浏览器访问你的 Harbor，进入 `codetalk` 项目，确认 5 个镜像都在：

```
codetalk/postgres:v1.0.0    ✓
codetalk/backend:v1.0.0     ✓
codetalk/deepwiki:v1.0.0    ✓
codetalk/gitnexus:v1.0.0    ✓
codetalk/frontend:v1.0.0    ✓
```

---

## 第四章 部署到 K8s

> **在哪执行**：内网机（能执行 kubectl 的那台）
>
> **目标**：5 个服务在 K8s 中跑起来

把项目的 `k8s/` 目录传到内网机上，进入该目录：

```bash
cd k8s/
ls
# 应该看到 6 个文件：
# 00-namespace.yaml
# 01-pvc.yaml
# 02-configmap.yaml
# 03-secret.yaml
# 04-deployments.yaml
# 05-services.yaml
```

下面按顺序修改和部署。

### 第 1 步：替换 Harbor 地址

所有 YAML 文件里有个占位符 `HARBOR_HOST`，需要替换成你实际的 Harbor 地址：

```bash
HARBOR=harbor.company.com
VERSION=v1.0.0

sed -i "s|HARBOR_HOST|$HARBOR|g" 04-deployments.yaml
sed -i "s|:VERSION|:$VERSION|g" 04-deployments.yaml
```

**验证替换成功**：

```bash
grep "image:" 04-deployments.yaml
```

输出应该类似：

```
image: harbor.company.com/codetalk/postgres:v1.0.0
image: harbor.company.com/codetalk/backend:v1.0.0
image: harbor.company.com/codetalk/deepwiki:v1.0.0
image: harbor.company.com/codetalk/gitnexus:v1.0.0
image: harbor.company.com/codetalk/frontend:v1.0.0
```

如果还看到 `HARBOR_HOST` 或 `VERSION`，说明替换没成功，手动编辑修改。

### 第 2 步：配置存储

打开 `01-pvc.yaml`，找到 `code-volume` 这个 PVC（大约第 15-25 行），修改 `storageClassName`：

**如果你有 RWX StorageClass**（比如在第一章装了 NFS）：

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: code-volume
  namespace: codetalk
spec:
  accessModes:
    - ReadWriteMany          # ← 不要改
  resources:
    requests:
      storage: 50Gi
  storageClassName: nfs-rwx  # ← 填你的 StorageClass 名字
```

**如果你是单节点 workaround**（第一章 1.3 节选的办法 B）：

```yaml
  accessModes:
    - ReadWriteOnce          # ← 改成 ReadWriteOnce
  storageClassName: ""       # ← 用默认 StorageClass，或填你集群的名字
```

同时你还需要在 `04-deployments.yaml` 中，给 backend、deepwiki、gitnexus 三个 Deployment 都加上 nodeSelector。在每个 Deployment 的 `spec.template.spec` 下面加：

```yaml
      nodeSelector:
        kubernetes.io/hostname: <你的节点名>   # kubectl get nodes 看名字
```

### 第 3 步：配置前端访问地址

这一步非常重要。前端是在用户的浏览器里运行的，它需要知道后端 API 的地址。

**先查你的 K8s 节点 IP**：

```bash
kubectl get nodes -o wide
# 看 INTERNAL-IP 那一列，记下其中一个节点的 IP
```

**编辑 `02-configmap.yaml`**，找到 `REPLACE_WITH_NODE_IP`，替换为刚才查到的 IP：

```bash
NODE_IP=10.x.x.x   # ← 换成你的实际节点 IP

sed -i "s|REPLACE_WITH_NODE_IP|$NODE_IP|g" 02-configmap.yaml
```

**验证**：

```bash
grep "NODE_IP\|API_URL\|WS_URL" 02-configmap.yaml
```

输出应该是：

```
NEXT_PUBLIC_API_URL: "http://10.x.x.x:30800"
NEXT_PUBLIC_WS_URL: "ws://10.x.x.x:30800"
```

> 解释：用户浏览器访问前端（30003 端口），前端页面里的 JavaScript 需要调用后端 API（30800 端口）。这个 URL 必须是用户浏览器能直接访问到的地址。

### 第 4 步：生成密钥

编辑 `03-secret.yaml`，需要填三个值。

**生成 PostgreSQL 密码**（你自己定一个强密码）：

```bash
echo -n "MyStr0ngP@ssw0rd" | base64
# 输出类似：TXlTdHIwbmdQQHNzdzByZA==
```

**生成 Fernet 密钥**（用于加密存储 API Key）：

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 输出类似：abCdEfGhIjKlMnOpQrStUvWxYz1234567890ABCD=
# 再 base64 编码：
echo -n "上面输出的那串" | base64
```

> 如果内网机没有 python3 和 cryptography 库，可以在外网机上生成这个密钥，然后一起带过来。

**编辑 `03-secret.yaml`**，把占位符替换为你生成的 base64 值：

```yaml
data:
  POSTGRES_PASSWORD: TXlTdHIwbmdQQHNzdzByZA==   # ← 替换
  FERNET_KEY: <你生成的base64编码的Fernet密钥>     # ← 替换
  OPENAI_API_KEY: ""           # 如果有内网 LLM 代理的 key，填这里
  ANTHROPIC_API_KEY: ""        # 没有就留空
  GOOGLE_API_KEY: ""           # 没有就留空
  DEEPWIKI_EMBEDDING_API_KEY: "" # 和 OPENAI_API_KEY 填一样的
```

> **安全提醒**：这个文件包含密码，不要提交到 Git 仓库。

### 第 5 步：配置 LLM（可选）

如果你的内网有 LLM 服务（如 Ollama 或 OpenAI 兼容的代理），编辑 `02-configmap.yaml`：

**使用 Ollama**：
```yaml
  DEEPWIKI_EMBEDDER_TYPE: "ollama"
  OLLAMA_BASE_URL: "http://<Ollama服务IP>:11434"
```

**使用 OpenAI 兼容代理**：
```yaml
  DEEPWIKI_EMBEDDER_TYPE: "openai"
  # 在 03-secret.yaml 中填写对应的 API Key
```

**没有 LLM 服务**：不用改，AI 问答功能不可用，但代码图谱、搜索、安全扫描等功能不受影响。

### 第 6 步：配置 Harbor 认证（如果 Harbor 需要登录）

如果你的 Harbor 需要认证才能拉镜像，先创建认证密钥：

```bash
kubectl create namespace codetalk 2>/dev/null || true

kubectl create secret docker-registry harbor-cred \
  --docker-server=harbor.company.com \
  --docker-username=你的用户名 \
  --docker-password=你的密码 \
  -n codetalk
```

然后编辑 `04-deployments.yaml`，在**每个** Deployment 和 StatefulSet 的 `spec.template.spec` 下面加：

```yaml
      imagePullSecrets:
        - name: harbor-cred
```

一共需要加 5 处（postgres、backend、deepwiki、gitnexus、frontend）。

> 如果 Harbor 不需要认证（所有人都能拉镜像），跳过这一步。

### 第 7 步：部署！

现在，按顺序执行：

```bash
kubectl apply -f 00-namespace.yaml
echo "✓ 命名空间创建完成"

kubectl apply -f 01-pvc.yaml
echo "✓ 存储卷创建完成"

kubectl apply -f 02-configmap.yaml
echo "✓ 配置文件创建完成"

kubectl apply -f 03-secret.yaml
echo "✓ 密钥创建完成"

kubectl apply -f 04-deployments.yaml
echo "✓ 服务部署完成"

kubectl apply -f 05-services.yaml
echo "✓ 网络服务创建完成"
```

### 第 8 步：等待所有 Pod 启动

```bash
kubectl get pods -n codetalk -w
```

观察 STATUS 列，等待所有 Pod 变成 `Running`。预计等待时间：

| Pod | 启动时间 | 正常状态 |
|-----|---------|---------|
| postgres-0 | ~15 秒 | Running (1/1) |
| backend-xxx | ~30 秒 | Running (1/1) |
| gitnexus-xxx | ~30 秒 | Running (1/1) |
| deepwiki-xxx | ~60 秒 | Running (1/1) |
| frontend-xxx | ~20 秒 | Running (1/1) |

> 按 `Ctrl+C` 退出 watch 模式。

**如果某个 Pod 状态是 `ImagePullBackOff`**：说明拉镜像失败，看[第八章 问题 1](#问题-1pod-状态-imagepullbackoff)。

**如果某个 Pod 状态是 `CrashLoopBackOff`**：说明启动失败，看[第八章 问题 2](#问题-2pod-状态-crashloopbackoff)。

### 第 9 步：初始化数据库

数据库需要执行一次迁移脚本，创建表结构：

```bash
# 找到 backend Pod 的名字
BACKEND=$(kubectl get pod -n codetalk -l app=backend -o jsonpath='{.items[0].metadata.name}')

# 执行数据库迁移
kubectl exec -n codetalk $BACKEND -- alembic upgrade head
```

看到类似 `INFO  [alembic.runtime.migration] Running upgrade ...` 就是成功。

**到这里，部署完成了。**

---

## 第五章 验证部署是否成功

### 检查 1：后端 API 能访问

```bash
NODE_IP=<你在第4步填的那个节点IP>

curl http://$NODE_IP:30800/health
```

返回 `{"status":"ok"}` 就是正常。

### 检查 2：工具服务都在线

```bash
BACKEND=$(kubectl get pod -n codetalk -l app=backend -o jsonpath='{.items[0].metadata.name}')

# 检查 DeepWiki
kubectl exec -n codetalk $BACKEND -- curl -s http://deepwiki:8001/api/health
# 应返回 {"status": "ok"} 或类似

# 检查 GitNexus
kubectl exec -n codetalk $BACKEND -- curl -s http://gitnexus:7100/health
# 应返回健康状态
```

### 检查 3：打开前端页面

用浏览器访问：

```
http://<节点IP>:30003
```

你应该看到 CodeTalks 的仪表盘页面（深色背景，显示项目数 0、任务数 0）。

### 检查 4：API 连通性

在浏览器 DevTools（F12）→ Console 中查看有没有红色错误。

常见问题：
- **CORS 错误**：`NEXT_PUBLIC_API_URL` 配置不对，检查 ConfigMap
- **连接拒绝**：节点 IP 或端口不对
- **超时**：防火墙拦了 30800 端口

如果 4 项检查都通过，恭喜，部署成功！

---

## 第六章 怎么把代码导入系统

部署好之后，你需要把待分析的代码导入 CodeTalks。有两种方式。

### 方式 A：通过内网 Git 地址导入（推荐）

**前提**：你公司有内网 GitLab/Gitea，待分析的代码已经在上面。

操作步骤：

1. 打开 CodeTalks（`http://<节点IP>:30003`）
2. 点击左侧导航栏的 **「资产」**
3. 点击右上角 **「新建项目」**，输入项目名称，点击创建
4. 在右栏点击 **「添加仓库」**
5. 选择 **「Git URL」** 标签页
6. 填写：
   - 仓库名称：随便取个名字（如 `my-backend`）
   - Git URL：填内网 Git 地址（如 `http://gitlab.internal/team/backend.git`）
   - 分支：填要分析的分支（默认 `main`）
7. 点击 **「添加」**
8. 在仓库列表中点击 **「同步」** 按钮
9. 等待同步完成（进度图标停止旋转）

> 如果 Git 仓库需要认证（私有仓库），URL 格式用：
> `http://用户名:密码@gitlab.internal/team/backend.git`
> 或
> `http://oauth2:access_token@gitlab.internal/team/backend.git`

### 方式 B：手动放到共享目录

**前提**：代码无法放到内网 Git，需要手动拷贝。

操作步骤：

**第一步**：在 CodeTalks 中先创建仓库记录

1. 资产 → 新建项目 → 添加仓库
2. 选择 **「本地路径」** 标签页
3. 填写仓库名称和一个路径名（如 `my-project`）
4. 添加后，**不要点同步**

**第二步**：把代码拷贝到共享存储

```bash
# 创建临时 Pod 来访问共享存储
kubectl run copy-helper \
  --image=harbor.company.com/codetalk/backend:v1.0.0 \
  --restart=Never \
  -n codetalk \
  --overrides='{
    "spec":{
      "volumes":[{"name":"code","persistentVolumeClaim":{"claimName":"code-volume"}}],
      "containers":[{
        "name":"copy-helper",
        "image":"harbor.company.com/codetalk/backend:v1.0.0",
        "command":["sleep","3600"],
        "volumeMounts":[{"name":"code","mountPath":"/data/repos"}]
      }]
    }
  }'

# 等 Pod 启动
kubectl wait --for=condition=Ready pod/copy-helper -n codetalk --timeout=60s

# 在共享存储里创建目录（路径要和你在 UI 里填的一致）
kubectl exec -n codetalk copy-helper -- mkdir -p /data/repos/my-project

# 把代码拷进去
kubectl cp ./你的代码目录/ codetalk/copy-helper:/data/repos/my-project/

# 确认文件已到位
kubectl exec -n codetalk copy-helper -- ls /data/repos/my-project/

# 清理临时 Pod
kubectl delete pod copy-helper -n codetalk
```

**第三步**：回到 CodeTalks UI，点击仓库的 **「同步」** 按钮

### 导入后：创建分析任务

代码同步完成后：

1. 在仓库列表中点击 **「分析」** 按钮
2. 选择要使用的分析工具
3. 点击创建
4. 去 **「任务」** 页面查看进度
5. 任务完成后，回到仓库详情页就能看到文档、图谱、搜索结果了

---

## 第七章 日常运维操作

### 7.1 查看 Pod 状态

```bash
kubectl get pods -n codetalk
```

所有 Pod 的 STATUS 应该是 `Running`，RESTARTS 应该是 0（或很小的数字）。

### 7.2 查看日志

```bash
# 查看后端日志（最近 100 行，实时跟踪）
kubectl logs -n codetalk -l app=backend --tail=100 -f

# 查看其他服务日志
kubectl logs -n codetalk -l app=deepwiki --tail=100 -f
kubectl logs -n codetalk -l app=gitnexus --tail=100 -f
kubectl logs -n codetalk -l app=frontend --tail=100 -f
kubectl logs -n codetalk -l app=postgres --tail=100 -f
```

> 按 `Ctrl+C` 停止实时跟踪。

### 7.3 重启某个服务

```bash
# 重启后端
kubectl rollout restart deployment/backend -n codetalk

# 重启 DeepWiki
kubectl rollout restart deployment/deepwiki -n codetalk

# 重启 GitNexus
kubectl rollout restart deployment/gitnexus -n codetalk

# 重启前端
kubectl rollout restart deployment/frontend -n codetalk

# 重启 PostgreSQL（StatefulSet，会短暂不可用）
kubectl rollout restart statefulset/postgres -n codetalk
```

### 7.4 修改配置

**修改普通配置**（ConfigMap）：

```bash
kubectl edit configmap codetalk-config -n codetalk
# 编辑器会打开，修改后保存退出

# 然后重启受影响的服务
kubectl rollout restart deployment/backend -n codetalk
kubectl rollout restart deployment/frontend -n codetalk  # 如果改了 NEXT_PUBLIC 开头的配置
```

**修改密钥**（Secret）：

```bash
kubectl edit secret codetalk-secret -n codetalk
# 注意：值需要是 base64 编码的

# 重启
kubectl rollout restart deployment/backend -n codetalk
```

### 7.5 升级版本

当有新版本时：

```bash
# 1. 在外网机构建新版镜像（假设新版本 v1.1.0）
VERSION=v1.1.0
# 重复第二章的构建步骤

# 2. 在内网导入新镜像（重复第三章步骤）

# 3. 滚动更新（不停服）
kubectl set image deployment/backend \
  backend=harbor.company.com/codetalk/backend:v1.1.0 -n codetalk

kubectl set image deployment/deepwiki \
  deepwiki=harbor.company.com/codetalk/deepwiki:v1.1.0 -n codetalk

kubectl set image deployment/gitnexus \
  gitnexus=harbor.company.com/codetalk/gitnexus:v1.1.0 -n codetalk

kubectl set image deployment/frontend \
  frontend=harbor.company.com/codetalk/frontend:v1.1.0 -n codetalk

# 4. 如果后端有数据库变更，执行迁移
BACKEND=$(kubectl get pod -n codetalk -l app=backend -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n codetalk $BACKEND -- alembic upgrade head
```

### 7.6 配置 LLM 模型

部署完成后，在 CodeTalks 界面里配置 LLM：

1. 打开 **设置** 页面
2. 找到 **LLM 配置** 区域
3. 点击 **新增配置**：
   - 提供商：选择 `Ollama` 或 `Custom（兼容 OpenAI）`
   - Base URL：填内网 LLM 服务地址（如 `http://10.0.0.100:11434/v1`）
   - 模型名称：填模型名（如 `qwen2.5-72b`）
   - API Key：如果需要就填，不需要就留空
   - 代理模式：选 **直连**
4. 点击 **测试** 验证是否可用
5. 测试通过后点击 **设为默认**

### 7.7 K8s 中不可用的功能

以下功能在 Docker Compose 模式下可用，但 K8s 模式下不可用：

| 功能 | 原因 | 替代方案 |
|------|------|---------|
| 设置页面的「组件配置」 | 依赖 Docker Engine API，K8s 无此概念 | 用 `kubectl edit` 修改 ConfigMap/Secret |
| 工具页面的「重启」按钮 | 同上 | 用 `kubectl rollout restart` |

---

## 第八章 出了问题怎么办

### 问题 1：Pod 状态 ImagePullBackOff

**含义**：Pod 拉镜像失败。

**排查**：

```bash
kubectl describe pod -n codetalk <Pod名字> | tail -20
```

看最后的 Events 部分。

| 错误信息 | 原因 | 解决 |
|---------|------|------|
| `HARBOR_HOST/codetalk/xxx` | 占位符没替换 | 重新执行第四章第 1 步 |
| `unauthorized` 或 `access denied` | Harbor 认证失败 | 执行第四章第 6 步 |
| `not found` | 镜像不在 Harbor | 重新执行第三章推送步骤 |
| `dial tcp ... timeout` | 网络不通 | 检查节点到 Harbor 的网络 |

### 问题 2：Pod 状态 CrashLoopBackOff

**含义**：Pod 启动了但立刻挂掉，K8s 反复重启它。

**排查**：

```bash
# 看这个 Pod 的日志
kubectl logs -n codetalk <Pod名字> --previous
```

| 常见日志 | 原因 | 解决 |
|---------|------|------|
| `Connection refused ... postgres` | 数据库还没启动好 | 等 postgres Pod Ready 后，删除 backend Pod 让它重建 |
| `password authentication failed` | 密码不匹配 | 检查 ConfigMap 的 DATABASE_URL 和 Secret 的 POSTGRES_PASSWORD 是否对应 |
| `No module named 'xxx'` | backend 镜像构建有问题 | 重新构建 backend 镜像 |

### 问题 3：前端页面打开是白屏

**排查**：

```bash
# 查看前端日志
kubectl logs -n codetalk -l app=frontend --tail=50
```

| 原因 | 解决 |
|------|------|
| 端口不对 | 确认用的是 30003 端口 |
| API 地址没替换 | 检查 ConfigMap 的 NEXT_PUBLIC_API_URL，重启 frontend |
| Pod 没 Running | `kubectl get pods -n codetalk` 检查状态 |

### 问题 4：前端能打开但 API 报错

浏览器 F12 → Console，看错误类型：

| 错误 | 原因 | 解决 |
|------|------|------|
| `CORS policy` | 跨域问题 | NEXT_PUBLIC_API_URL 的 IP 或端口不对 |
| `net::ERR_CONNECTION_REFUSED` | 后端 Pod 没运行 | 检查 backend Pod 状态 |
| `502 Bad Gateway` | 后端在重启中 | 等一会再试 |

### 问题 5：DeepWiki 文档生成失败

```bash
kubectl logs -n codetalk -l app=deepwiki --tail=50
```

| 原因 | 解决 |
|------|------|
| `No such file or directory` | 共享卷挂载有问题，检查 PVC 状态 |
| `API key not found` / `401` | LLM API Key 没配或配错了 |
| 没有任何 LLM 端点 | 正常。没 LLM 就用不了 AI 功能 |

### 问题 6：数据库迁移报错

```bash
BACKEND=$(kubectl get pod -n codetalk -l app=backend -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n codetalk $BACKEND -- alembic upgrade head
```

| 错误 | 解决 |
|------|------|
| `could not connect to server` | postgres Pod 还没 Ready，等一下再试 |
| `relation already exists` | 说明迁移已经执行过了，不用再跑 |
| `FATAL: password authentication failed` | Secret 里的密码和 ConfigMap 里 DATABASE_URL 不匹配 |

### 问题 7：知识图谱（GitNexus）不显示数据

可能原因：
1. 没有运行 GitNexus 分析任务 → 去任务页面创建一个
2. GitNexus Pod 不健康 → `kubectl logs -n codetalk -l app=gitnexus --tail=50`
3. 共享卷中没有代码 → 检查是否成功同步

### 问题 8：想完全重来

```bash
# 删除所有资源（数据也会丢！）
kubectl delete namespace codetalk

# 重新从第四章第 7 步开始
```

> 这会删除所有数据（数据库、分析结果、克隆的代码）。只在确实需要从头开始时使用。

---

## 附录 A：部署后的端口总览

| 服务 | 集群内地址（Pod 之间用） | 外部访问地址（浏览器用） |
|------|------------------------|----------------------|
| 前端 | `frontend:3000` | `http://<节点IP>:30003` |
| 后端 API | `backend:8000` | `http://<节点IP>:30800` |
| DeepWiki Web | `deepwiki:3000` | `http://<节点IP>:30300`（可选） |
| DeepWiki API | `deepwiki:8001` | 不对外暴露 |
| GitNexus | `gitnexus:7100` | 不对外暴露 |
| PostgreSQL | `postgres:5432` | 不对外暴露 |

> 只有前端（30003）和后端 API（30800）需要用户能访问到。其他服务都是 Pod 之间内部通信。

## 附录 B：完整配置参考

### ConfigMap（`02-configmap.yaml`）关键字段

| 字段 | 必须修改 | 说明 |
|------|---------|------|
| `NEXT_PUBLIC_API_URL` | ✅ 是 | 浏览器访问后端的地址，格式 `http://<节点IP>:30800` |
| `NEXT_PUBLIC_WS_URL` | ✅ 是 | WebSocket 地址，格式 `ws://<节点IP>:30800` |
| `POSTGRES_USER` | 可选 | 默认 `codetalks`，一般不用改 |
| `POSTGRES_DB` | 可选 | 默认 `codetalks`，一般不用改 |
| `DEEPWIKI_EMBEDDER_TYPE` | 可选 | `openai` 或 `ollama`，取决于你有什么 LLM |
| `OLLAMA_BASE_URL` | 可选 | 如果用 Ollama，填地址 |

### Secret（`03-secret.yaml`）关键字段

| 字段 | 必须修改 | 说明 |
|------|---------|------|
| `POSTGRES_PASSWORD` | ✅ 是 | 数据库密码（base64 编码） |
| `FERNET_KEY` | ✅ 是 | 加密密钥（base64 编码） |
| `OPENAI_API_KEY` | 可选 | 内网 LLM 代理的 Key |
| `DEEPWIKI_EMBEDDING_API_KEY` | 可选 | Embedding 服务的 Key，通常和 OPENAI_API_KEY 相同 |

## 附录 C：一键检查脚本

将以下内容保存为 `check.sh`，部署完成后执行 `bash check.sh` 自动检查：

```bash
#!/bin/bash
echo "========== CodeTalks 部署检查 =========="

echo ""
echo "[1/5] Pod 状态"
kubectl get pods -n codetalk -o wide
echo ""

echo "[2/5] PVC 状态"
kubectl get pvc -n codetalk
echo ""

echo "[3/5] Service 端口"
kubectl get svc -n codetalk
echo ""

NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

echo "[4/5] 后端健康检查"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://$NODE_IP:30800/health 2>/dev/null)
if [ "$HTTP_CODE" = "200" ]; then
  echo "  ✓ 后端 API 正常 (HTTP $HTTP_CODE)"
else
  echo "  ✗ 后端 API 异常 (HTTP $HTTP_CODE)"
fi
echo ""

echo "[5/5] 前端可访问性"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://$NODE_IP:30003 2>/dev/null)
if [ "$HTTP_CODE" = "200" ]; then
  echo "  ✓ 前端页面正常 (HTTP $HTTP_CODE)"
else
  echo "  ✗ 前端页面异常 (HTTP $HTTP_CODE)"
fi
echo ""

echo "========== 检查完成 =========="
echo "前端地址: http://$NODE_IP:30003"
echo "后端地址: http://$NODE_IP:30800"
```

---

*本手册覆盖 CodeTalks 8 个核心服务（PostgreSQL、Backend、DeepWiki、GitNexus、Frontend、Joern、Zoekt、CodeCompass）的部署。K8s 部署清单已包含在 `k8s/` 目录中（`04-deployments.yaml` 含全部 8 个 Deployment）。内网镜像导入流程见 `INTRANET_DEPLOY.md` 或 `k8s/DEPLOY.md`。*
