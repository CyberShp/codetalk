"""Kubernetes deployer -- deploys CodeTalk to a local Kind cluster."""

import asyncio
import base64
import os
import socket
import tempfile
from pathlib import Path
from typing import Optional

import yaml

try:
    import aiohttp
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

PROJECT_ROOT = Path(__file__).parent.parent.parent

TOTAL_STEPS = 8

# (name, k8s_port, health_kind, health_path, host_port)
SERVICES = [
    ("postgres",     5432,  "tcp",  None,       5433),
    ("backend",      8000,  "http", "/health",  8000),
    ("frontend",     3005,  "http", "/",        3005),
    ("deepwiki",     8001,  "tcp",  None,       8001),
    ("gitnexus",     7100,  "tcp",  None,       7100),
    ("joern",        8080,  "tcp",  None,       8080),
    ("codecompass",  6251,  "http", "/",        6251),
    ("zoekt",        6070,  "http", "/healthz", 6070),
]

# Services that need to be built from source
BUILD_IMAGES = [
    ("codetalk-backend",     "./backend"),
    ("codetalk-frontend",    "./frontend"),
    ("codetalk-gitnexus",    "./docker/gitnexus"),
    ("codetalk-codecompass", "./docker/codecompass"),
]

# Services that use pre-built images
PREBUILT_IMAGES = {
    "postgres":    "postgres:16",
    "deepwiki":    "ghcr.io/asyncfuncai/deepwiki-open:latest",
    "joern":       "ghcr.io/joernio/joern:nightly",
    "zoekt":       "ghcr.io/sourcegraph/zoekt:latest",
}

# Services that need PVCs: name -> (pvc_name, size, mount_path)
PVC_SERVICES = {
    "postgres":    ("pg-data",          "1Gi",  "/var/lib/postgresql/data"),
    "deepwiki":    ("deepwiki-data",    "5Gi",  "/root/.adalflow"),
    "gitnexus":    ("gitnexus-data",    "5Gi",  "/root/.gitnexus"),
    "joern":       ("joern-data",       "10Gi", "/root/.joern"),
    "codecompass": ("codecompass-data", "10Gi", "/data/workspaces"),
    "zoekt":       ("zoekt-index",      "10Gi", "/data/index"),
}

KIND_CONFIG = """kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - containerPort: 80
        hostPort: 80
        protocol: TCP
      - containerPort: 443
        hostPort: 443
        protocol: TCP
"""

INGRESS_NGINX_URL = (
    "https://raw.githubusercontent.com/kubernetes/ingress-nginx"
    "/main/deploy/static/provider/kind/deploy.yaml"
)


def _generate_fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet  # type: ignore
        return Fernet.generate_key().decode()
    except ImportError:
        return base64.urlsafe_b64encode(os.urandom(32)).decode()


class K8sDeployer:
    def __init__(self, config: dict, event_queue: asyncio.Queue) -> None:
        self._config = config
        self._queue = event_queue
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stopped = False

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def deploy(self) -> None:
        self._stopped = False
        try:
            await self._step_kind_cluster()
            if self._stopped:
                return
            await self._step_ingress()
            if self._stopped:
                return
            await self._step_namespace()
            if self._stopped:
                return
            await self._step_secrets()
            if self._stopped:
                return
            await self._step_build_images()
            if self._stopped:
                return
            await self._step_deploy_services()
            if self._stopped:
                return
            await self._step_wait_ready()
            if self._stopped:
                return
            await self._step_create_ingress()
        except Exception as exc:
            await self._emit("error", "error", f"Deployment failed: {exc}", 0)
            raise

    async def stop(self) -> None:
        self._stopped = True
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass

    async def check_health(self) -> list:
        """Check health of deployed services via kubectl pod status."""
        code, stdout = await self._run_capture(
            "kubectl", "get", "pods", "-n", "codetalk",
            "-o", "jsonpath={range .items[*]}{.metadata.labels.app}{','}{.status.phase}{','}{.status.containerStatuses[0].ready}{';'}{end}"
        )
        if code != 0:
            return [
                {"name": s[0], "healthy": False, "message": "kubectl not available"}
                for s in SERVICES
            ]

        pod_status: dict[str, tuple[str, bool]] = {}
        for entry in stdout.strip().split(";"):
            if not entry.strip():
                continue
            parts = entry.split(",")
            if len(parts) >= 3:
                app_name = parts[0]
                phase = parts[1]
                ready = parts[2].lower() == "true"
                pod_status[app_name] = (phase, ready)

        results = []
        for name, _k8s_port, _kind, _path, _host_port in SERVICES:
            if name in pod_status:
                phase, ready = pod_status[name]
                if ready:
                    results.append({"name": name, "healthy": True, "message": f"Pod running ({phase})"})
                else:
                    results.append({"name": name, "healthy": False, "message": f"Pod not ready ({phase})"})
            else:
                results.append({"name": name, "healthy": False, "message": "Pod not found"})
        return results

    # ------------------------------------------------------------------ #
    # Deployment steps                                                     #
    # ------------------------------------------------------------------ #

    async def _step_kind_cluster(self) -> None:
        step = 1
        await self._emit("kind_cluster", "running", "Checking for Kind cluster...", step)

        # Check if cluster already exists
        code, stdout = await self._run_capture("kind", "get", "clusters")
        if "codetalk" in stdout:
            await self._emit("kind_cluster", "done", "Kind cluster 'codetalk' already exists", step)
            return

        await self._emit("kind_cluster", "running", "Creating Kind cluster 'codetalk'...", step)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(KIND_CONFIG)
            kind_config_path = fh.name

        try:
            rc = await self._run_stream(
                "kind_cluster", step,
                "kind", "create", "cluster",
                "--name", "codetalk",
                "--config", kind_config_path,
            )
        finally:
            os.unlink(kind_config_path)

        if rc != 0:
            await self._emit("kind_cluster", "error", f"kind create cluster failed (exit {rc})", step)
            raise RuntimeError(f"kind create cluster exited with code {rc}")

        await self._emit("kind_cluster", "done", "Kind cluster 'codetalk' created", step)

    async def _step_ingress(self) -> None:
        step = 2
        await self._emit("ingress", "running", "Installing NGINX Ingress Controller...", step)

        rc = await self._run_stream(
            "ingress", step,
            "kubectl", "apply", "-f", INGRESS_NGINX_URL,
        )
        if rc != 0:
            await self._emit("ingress", "error", f"kubectl apply ingress failed (exit {rc})", step)
            raise RuntimeError(f"ingress install exited with code {rc}")

        await self._emit("ingress", "running", "Waiting for ingress-nginx pods to be ready (up to 120s)...", step)
        rc = await self._run_stream(
            "ingress", step,
            "kubectl", "wait",
            "--namespace", "ingress-nginx",
            "--for=condition=ready", "pod",
            "--selector=app.kubernetes.io/component=controller",
            "--timeout=120s",
        )
        if rc != 0:
            # Non-fatal: ingress pods might still be starting
            await self._emit(
                "ingress", "running",
                "Ingress pods not ready yet -- continuing (they may still be starting)", step,
            )
        else:
            await self._emit("ingress", "done", "NGINX Ingress Controller ready", step)

    async def _step_namespace(self) -> None:
        step = 3
        await self._emit("namespace", "running", "Creating namespace 'codetalk'...", step)

        rc = await self._run_stream(
            "namespace", step,
            "kubectl", "create", "namespace", "codetalk",
        )
        # rc 1 with "already exists" is acceptable
        if rc not in (0, 1):
            await self._emit("namespace", "error", f"kubectl create namespace failed (exit {rc})", step)
            raise RuntimeError(f"namespace creation exited with code {rc}")

        await self._emit("namespace", "done", "Namespace 'codetalk' ready", step)

    async def _step_secrets(self) -> None:
        step = 4
        await self._emit("secrets", "running", "Creating Kubernetes secrets...", step)

        cfg = self._config
        fernet_key = cfg.get("fernet_key") or _generate_fernet_key()

        pg_user = cfg.get("postgres_user", "codetalks")
        pg_password = cfg.get("postgres_password", "changeme")
        pg_db = cfg.get("postgres_db", "codetalks")

        secret_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "codetalk-secrets",
                "namespace": "codetalk",
            },
            "type": "Opaque",
            "stringData": {
                "POSTGRES_USER": pg_user,
                "POSTGRES_PASSWORD": pg_password,
                "POSTGRES_DB": pg_db,
                "FERNET_KEY": fernet_key,
                "OPENAI_API_KEY": cfg.get("openai_api_key", ""),
                "OPENAI_BASE_URL": cfg.get("openai_base_url", ""),
                "ANTHROPIC_API_KEY": cfg.get("anthropic_api_key", ""),
                "GOOGLE_API_KEY": cfg.get("google_api_key", ""),
                "DEEPWIKI_EMBEDDING_BASE_URL": cfg.get("deepwiki_embedding_base_url", ""),
                "DEEPWIKI_EMBEDDING_API_KEY": cfg.get(
                    "deepwiki_embedding_api_key",
                    cfg.get("openai_api_key", ""),
                ),
                "DEEPWIKI_EMBEDDER_TYPE": cfg.get("deepwiki_embedder_type", "openai"),
                "OLLAMA_BASE_URL": cfg.get("ollama_base_url", "http://host.docker.internal:11434"),
                "OLLAMA_HOST": cfg.get("ollama_host", "http://host.docker.internal:11434"),
            },
        }

        rc = await self._apply_manifest(secret_manifest, "secrets", step)
        if rc != 0:
            await self._emit("secrets", "error", f"Failed to create secrets (exit {rc})", step)
            raise RuntimeError(f"secret creation exited with code {rc}")

        await self._emit("secrets", "done", "Secrets created", step)

    async def _step_build_images(self) -> None:
        step = 5
        await self._emit("build_images", "running", "Building and loading Docker images into Kind...", step)

        for image_tag, build_context in BUILD_IMAGES:
            if self._stopped:
                return
            await self._emit(
                "build_images", "running",
                f"Building {image_tag} from {build_context}...", step,
            )
            build_path = str(PROJECT_ROOT / build_context.lstrip("./"))
            rc = await self._run_stream(
                "build_images", step,
                "docker", "build", "-t", image_tag, build_path,
            )
            if rc != 0:
                await self._emit(
                    "build_images", "error",
                    f"docker build {image_tag} failed (exit {rc})", step,
                )
                raise RuntimeError(f"docker build {image_tag} exited with code {rc}")

            await self._emit(
                "build_images", "running",
                f"Loading {image_tag} into Kind cluster...", step,
            )
            rc = await self._run_stream(
                "build_images", step,
                "kind", "load", "docker-image", image_tag, "--name", "codetalk",
            )
            if rc != 0:
                await self._emit(
                    "build_images", "error",
                    f"kind load {image_tag} failed (exit {rc})", step,
                )
                raise RuntimeError(f"kind load {image_tag} exited with code {rc}")

        await self._emit("build_images", "done", "All images built and loaded", step)

    async def _step_deploy_services(self) -> None:
        step = 6
        await self._emit("deploy_services", "running", "Generating and applying K8s manifests...", step)

        manifests_yaml = self._generate_manifests()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(manifests_yaml)
            manifest_path = fh.name

        try:
            rc = await self._run_stream(
                "deploy_services", step,
                "kubectl", "apply", "-f", manifest_path,
            )
        finally:
            os.unlink(manifest_path)

        if rc != 0:
            await self._emit("deploy_services", "error", f"kubectl apply failed (exit {rc})", step)
            raise RuntimeError(f"kubectl apply manifests exited with code {rc}")

        await self._emit("deploy_services", "done", "All K8s resources applied", step)

    async def _step_wait_ready(self) -> None:
        step = 7
        timeout_s = 300
        poll_interval = 5

        await self._emit(
            "wait_ready", "running",
            f"Waiting for all pods to be Running (timeout {timeout_s}s)...", step,
        )

        start = asyncio.get_event_loop().time()
        while True:
            if self._stopped:
                return

            _rc, output = await self._run_capture(
                "kubectl", "get", "pods", "-n", "codetalk", "--no-headers"
            )
            lines = [ln for ln in output.splitlines() if ln.strip()]

            if lines:
                not_running = [ln for ln in lines if not _pod_line_is_ready(ln)]
                if not not_running:
                    await self._emit(
                        "wait_ready", "done",
                        f"All {len(lines)} pods are Running/Ready", step,
                    )
                    return

                await self._emit(
                    "wait_ready", "running",
                    f"{len(not_running)}/{len(lines)} pods not yet ready...", step,
                )

            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= timeout_s:
                await self._emit(
                    "wait_ready", "error",
                    f"Pods did not become ready within {timeout_s}s", step,
                )
                raise RuntimeError("Pod readiness timeout exceeded")

            await asyncio.sleep(poll_interval)

    async def _step_create_ingress(self) -> None:
        step = 8
        await self._emit("create_ingress", "running", "Creating Ingress routes...", step)

        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "codetalk-ingress",
                "namespace": "codetalk",
                "annotations": {
                    "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
                    "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
                    "nginx.ingress.kubernetes.io/proxy-http-version": "1.1",
                    "nginx.ingress.kubernetes.io/upstream-hash-by": "$remote_addr",
                },
            },
            "spec": {
                "ingressClassName": "nginx",
                "rules": [
                    {
                        "http": {
                            "paths": [
                                {
                                    "path": "/api",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "backend",
                                            "port": {"number": 8000},
                                        }
                                    },
                                },
                                {
                                    "path": "/ws",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "backend",
                                            "port": {"number": 8000},
                                        }
                                    },
                                },
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "frontend",
                                            "port": {"number": 3005},
                                        }
                                    },
                                },
                            ]
                        }
                    }
                ],
            },
        }

        rc = await self._apply_manifest(ingress, "create_ingress", step)
        if rc != 0:
            await self._emit("create_ingress", "error", f"kubectl apply ingress failed (exit {rc})", step)
            raise RuntimeError(f"ingress apply exited with code {rc}")

        await self._emit(
            "create_ingress", "done",
            "Ingress created -- CodeTalk is accessible at http://localhost/", step,
        )

    # ------------------------------------------------------------------ #
    # Manifest generation                                                  #
    # ------------------------------------------------------------------ #

    def _generate_manifests(self) -> str:
        cfg = self._config
        pg_user = cfg.get("postgres_user", "codetalks")
        pg_password = cfg.get("postgres_password", "changeme")
        pg_db = cfg.get("postgres_db", "codetalks")

        docs = []

        # ConfigMap with non-secret env vars
        docs.append(self._configmap_manifest(pg_user, pg_password, pg_db))

        # PVCs
        docs.append(self._manifest_shared_repos_pvc())
        for _svc_name, (pvc_name, size, _mount) in PVC_SERVICES.items():
            docs.append(self._pvc_manifest(pvc_name, size))

        # Deployments + Services
        docs.append(self._deployment_postgres(pg_user, pg_password, pg_db))
        docs.append(self._service_manifest("postgres", 5432))

        docs.append(self._deployment_backend(pg_user, pg_password, pg_db))
        docs.append(self._service_manifest("backend", 8000))

        docs.append(self._deployment_frontend())
        docs.append(self._service_manifest("frontend", 3005))

        docs.append(self._deployment_deepwiki())
        docs.append(self._service_manifest("deepwiki", 8001))

        docs.append(self._deployment_gitnexus())
        docs.append(self._service_manifest("gitnexus", 7100))

        docs.append(self._deployment_joern())
        docs.append(self._service_manifest("joern", 8080))

        docs.append(self._deployment_codecompass(pg_user, pg_password, pg_db))
        docs.append(self._service_manifest("codecompass", 6251))

        docs.append(self._deployment_zoekt())
        docs.append(self._service_manifest("zoekt", 6070))

        return "---\n".join(yaml.dump(d, default_flow_style=False) for d in docs)

    def _configmap_manifest(
        self, pg_user: str, pg_password: str, pg_db: str
    ) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "codetalk-config", "namespace": "codetalk"},
            "data": {
                "DATABASE_URL": (
                    f"postgresql+asyncpg://{pg_user}:{pg_password}"
                    f"@postgres:5432/{pg_db}"
                ),
                "REPOS_BASE_PATH": "/data/repos",
                "DEEPWIKI_BASE_URL": "http://deepwiki:8001",
                "GITNEXUS_BASE_URL": "http://gitnexus:7100",
                "ZOEKT_BASE_URL": "http://zoekt:6070",
                "JOERN_BASE_URL": "http://joern:8080",
                "CODECOMPASS_BASE_URL": "http://codecompass:6251",
            },
        }

    def _pvc_manifest(self, name: str, size: str) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": name, "namespace": "codetalk"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": size}},
            },
        }

    def _manifest_shared_repos_pvc(self) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "shared-repos", "namespace": "codetalk"},
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": "5Gi"}},
            },
        }

    def _service_manifest(self, name: str, port: int) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": "codetalk"},
            "spec": {
                "selector": {"app": name},
                "ports": [{"port": port, "targetPort": port, "protocol": "TCP"}],
                "type": "ClusterIP",
            },
        }

    def _base_deployment(
        self,
        name: str,
        image: str,
        port: int,
        env: list,
        volume_mounts: list,
        volumes: list,
        resources: Optional[dict] = None,
        command: Optional[list] = None,
        image_pull_policy: str = "IfNotPresent",
    ) -> dict:
        container: dict = {
            "name": name,
            "image": image,
            "imagePullPolicy": image_pull_policy,
            "ports": [{"containerPort": port}],
            "env": env,
        }
        if volume_mounts:
            container["volumeMounts"] = volume_mounts
        if resources:
            container["resources"] = resources
        if command:
            container["command"] = command

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": "codetalk"},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {"labels": {"app": name}},
                    "spec": {
                        "containers": [container],
                        "volumes": volumes,
                    },
                },
            },
        }

    def _deployment_postgres(
        self, pg_user: str, pg_password: str, pg_db: str
    ) -> dict:
        pvc_name, _size, mount_path = PVC_SERVICES["postgres"]
        return self._base_deployment(
            name="postgres",
            image="postgres:16",
            port=5432,
            env=[
                {"name": "POSTGRES_USER",     "value": pg_user},
                {"name": "POSTGRES_PASSWORD", "value": pg_password},
                {"name": "POSTGRES_DB",       "value": pg_db},
            ],
            volume_mounts=[{"name": "data", "mountPath": mount_path}],
            volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": pvc_name}}],
            image_pull_policy="Always",
        )

    def _deployment_backend(
        self, pg_user: str, pg_password: str, pg_db: str
    ) -> dict:
        return self._base_deployment(
            name="backend",
            image="codetalk-backend:latest",
            port=8000,
            env=[
                {
                    "name": "DATABASE_URL",
                    "value": (
                        f"postgresql+asyncpg://{pg_user}:{pg_password}"
                        f"@postgres:5432/{pg_db}"
                    ),
                },
                {"name": "REPOS_BASE_PATH",     "value": "/data/repos"},
                {"name": "DEEPWIKI_BASE_URL",    "value": "http://deepwiki:8001"},
                {"name": "GITNEXUS_BASE_URL",    "value": "http://gitnexus:7100"},
                {"name": "ZOEKT_BASE_URL",       "value": "http://zoekt:6070"},
                {"name": "JOERN_BASE_URL",       "value": "http://joern:8080"},
                {"name": "CODECOMPASS_BASE_URL", "value": "http://codecompass:6251"},
                {
                    "name": "FERNET_KEY",
                    "valueFrom": {
                        "secretKeyRef": {"name": "codetalk-secrets", "key": "FERNET_KEY"}
                    },
                },
                {
                    "name": "OPENAI_API_KEY",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": "codetalk-secrets", "key": "OPENAI_API_KEY", "optional": True,
                        }
                    },
                },
                {
                    "name": "ANTHROPIC_API_KEY",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": "codetalk-secrets", "key": "ANTHROPIC_API_KEY", "optional": True,
                        }
                    },
                },
            ],
            volume_mounts=[{"name": "repos", "mountPath": "/data/repos"}],
            volumes=[{"name": "repos", "persistentVolumeClaim": {"claimName": "shared-repos"}}],
            image_pull_policy="Never",
        )

    def _deployment_frontend(self) -> dict:
        return self._base_deployment(
            name="frontend",
            image="codetalk-frontend:latest",
            port=3005,
            env=[
                {"name": "NEXT_PUBLIC_API_URL", "value": "http://localhost/api"},
                {"name": "NEXT_PUBLIC_WS_URL",  "value": "ws://localhost/ws"},
            ],
            volume_mounts=[],
            volumes=[],
            image_pull_policy="Never",
        )

    def _deployment_deepwiki(self) -> dict:
        pvc_name, _size, mount_path = PVC_SERVICES["deepwiki"]
        return self._base_deployment(
            name="deepwiki",
            image="ghcr.io/asyncfuncai/deepwiki-open:latest",
            port=8001,
            env=[
                {
                    "name": key,
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": "codetalk-secrets", "key": key, "optional": True,
                        }
                    },
                }
                for key in [
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "DEEPWIKI_EMBEDDING_BASE_URL",
                    "DEEPWIKI_EMBEDDING_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GOOGLE_API_KEY",
                    "DEEPWIKI_EMBEDDER_TYPE",
                    "OLLAMA_BASE_URL",
                    "OLLAMA_HOST",
                ]
            ],
            volume_mounts=[
                {"name": "data",  "mountPath": mount_path},
                {"name": "repos", "mountPath": "/data/repos", "readOnly": True},
            ],
            volumes=[
                {"name": "data",  "persistentVolumeClaim": {"claimName": pvc_name}},
                {"name": "repos", "persistentVolumeClaim": {"claimName": "shared-repos"}},
            ],
            image_pull_policy="Always",
        )

    def _deployment_gitnexus(self) -> dict:
        pvc_name, _size, mount_path = PVC_SERVICES["gitnexus"]
        return self._base_deployment(
            name="gitnexus",
            image="codetalk-gitnexus:latest",
            port=7100,
            env=[],
            volume_mounts=[
                {"name": "data",  "mountPath": mount_path},
                {"name": "repos", "mountPath": "/data/repos"},
            ],
            volumes=[
                {"name": "data",  "persistentVolumeClaim": {"claimName": pvc_name}},
                {"name": "repos", "persistentVolumeClaim": {"claimName": "shared-repos"}},
            ],
            image_pull_policy="Never",
        )

    def _deployment_joern(self) -> dict:
        pvc_name, _size, mount_path = PVC_SERVICES["joern"]
        return self._base_deployment(
            name="joern",
            image="ghcr.io/joernio/joern:nightly",
            port=8080,
            env=[
                {"name": "JAVA_TOOL_OPTIONS", "value": "-Xmx6g"},
            ],
            volume_mounts=[
                {"name": "repos", "mountPath": "/data/repos", "readOnly": True},
                {"name": "data",  "mountPath": mount_path},
            ],
            volumes=[
                {"name": "repos", "persistentVolumeClaim": {"claimName": "shared-repos"}},
                {"name": "data",  "persistentVolumeClaim": {"claimName": pvc_name}},
            ],
            resources={
                "limits":   {"memory": "8Gi"},
                "requests": {"memory": "2Gi"},
            },
            command=["joern", "--server", "--server-host", "0.0.0.0", "--server-port", "8080"],
            image_pull_policy="Always",
        )

    def _deployment_codecompass(
        self, pg_user: str, pg_password: str, pg_db: str
    ) -> dict:
        pvc_name, _size, mount_path = PVC_SERVICES["codecompass"]
        return self._base_deployment(
            name="codecompass",
            image="codetalk-codecompass:latest",
            port=6251,
            env=[
                {
                    "name": "CC_DATABASE",
                    "value": (
                        f"pgsql:host=postgres;port=5432;"
                        f"user={pg_user};password={pg_password};database={pg_db}"
                    ),
                },
            ],
            volume_mounts=[
                {"name": "repos",      "mountPath": "/data/repos", "readOnly": True},
                {"name": "workspaces", "mountPath": mount_path},
            ],
            volumes=[
                {"name": "repos",      "persistentVolumeClaim": {"claimName": "shared-repos"}},
                {"name": "workspaces", "persistentVolumeClaim": {"claimName": pvc_name}},
            ],
            resources={
                "limits":   {"memory": "4Gi"},
                "requests": {"memory": "512Mi"},
            },
            image_pull_policy="Never",
        )

    def _deployment_zoekt(self) -> dict:
        pvc_name, _size, _mount = PVC_SERVICES["zoekt"]
        return self._base_deployment(
            name="zoekt",
            image="ghcr.io/sourcegraph/zoekt:latest",
            port=6070,
            env=[],
            volume_mounts=[
                {"name": "index", "mountPath": "/data/index"},
                {"name": "repos", "mountPath": "/data/repos", "readOnly": True},
            ],
            volumes=[
                {"name": "index", "persistentVolumeClaim": {"claimName": pvc_name}},
                {"name": "repos", "persistentVolumeClaim": {"claimName": "shared-repos"}},
            ],
            command=["zoekt-webserver", "-index", "/data/index", "-listen", ":6070", "-rpc"],
            image_pull_policy="Always",
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _apply_manifest(self, manifest: dict, step_name: str, step_index: int) -> int:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as fh:
            yaml.dump(manifest, fh, default_flow_style=False)
            path = fh.name
        try:
            return await self._run_stream(step_name, step_index, "kubectl", "apply", "-f", path)
        finally:
            os.unlink(path)

    async def _run_stream(
        self, step_name: str, step_index: int, *cmd: str
    ) -> int:
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        assert self._process.stdout is not None
        async for raw_line in self._process.stdout:
            if self._stopped:
                break
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                await self._queue.put({
                    "step": step_name,
                    "status": "running",
                    "message": line,
                    "progress": {"current": step_index, "total": TOTAL_STEPS},
                })
        await self._process.wait()
        return self._process.returncode or 0

    async def _run_capture(self, *cmd: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _stderr = await proc.communicate()
        rc = proc.returncode or 0
        return rc, stdout_bytes.decode(errors="replace")

    async def _emit(
        self, step: str, status: str, message: str, step_index: int
    ) -> None:
        await self._queue.put({
            "step": step,
            "status": status,
            "message": message,
            "progress": {"current": step_index, "total": TOTAL_STEPS},
        })

    @staticmethod
    async def _probe(
        session, name: str, port: int, kind: str, path: Optional[str]
    ) -> tuple[bool, str]:
        if kind == "tcp":
            loop = asyncio.get_event_loop()
            try:
                conn = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: socket.create_connection(("localhost", port), timeout=3),
                    ),
                    timeout=5,
                )
                conn.close()
                return True, "TCP connection OK"
            except Exception as exc:
                return False, str(exc)
        else:
            url = f"http://localhost:{port}{path}"
            try:
                async with session.get(url) as resp:
                    if resp.status < 500:
                        return True, f"HTTP {resp.status}"
                    return False, f"HTTP {resp.status}"
            except Exception as exc:
                return False, str(exc)


# ------------------------------------------------------------------ #
# Module-level helpers                                                 #
# ------------------------------------------------------------------ #

def _pod_line_is_ready(line: str) -> bool:
    """Return True if a kubectl get pods --no-headers line shows a ready pod."""
    # Columns: NAME  READY  STATUS  RESTARTS  AGE
    parts = line.split()
    if len(parts) < 3:
        return False
    status = parts[2]
    if status not in ("Running", "Completed"):
        return False
    # READY column e.g. "1/1" or "2/2"
    ready_col = parts[1]
    if "/" in ready_col:
        try:
            current, total = ready_col.split("/", 1)
            return int(current) >= int(total) and int(total) > 0
        except ValueError:
            return False
    return False
