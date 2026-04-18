---
feature_ids: []
related_features: []
topics: [runtime, local-dev, ports, restart, troubleshooting]
doc_kind: ops-note
created: 2026-04-17
---

# Local Runtime Rules

> Scope: host-run frontend/backend + Dockerized dependencies in this workspace.

## Current Local Port Map

As of 2026-04-17, the observed listeners are:

| Service | Browser/Host Port | Notes |
|---|---:|---|
| Frontend (Next.js) | `3005` | User explicitly requested `3005` instead of `3003` |
| Backend (FastAPI) | `8000` | Host-run Python process |
| PostgreSQL | `5433` | Docker `5432` exposed to host `5433` |
| Zoekt | `6070` | Dockerized |
| deepwiki-open | `8001` | Dockerized |
| GitNexus | `7100` | Dockerized |
| Joern | `8080` | Dockerized (CPG server, 8G memory limit) |
| Semgrep | `9090` | Dockerized (FastAPI wrapper over CLI) |

## Root Cause Summary For "unable to fetch"

The highest-confidence root cause is **runtime configuration drift**, not task-detail page logic:

1. Frontend task detail calls `GET /api/tasks/:id` through `NEXT_PUBLIC_API_URL` or the fallback `http://localhost:8000`.
2. Backend settings only auto-load `.env` from the **current working directory**.
3. The running backend process has cwd = `/Volumes/Media/codetalk/backend`.
4. There is **no** `backend/.env`; the real file is repo-root `/.env`.
5. Therefore, a host-run backend restarted from `backend/` will silently fall back to defaults unless env vars are exported manually.

Direct evidence:

- `frontend/src/lib/api.ts` defaults to `http://localhost:8000`
- `backend/app/config.py` uses `model_config = {"env_file": ".env", ...}`
- running backend cwd is `/Volumes/Media/codetalk/backend`
- `backend/.env` does not exist
- repo-root `.env` uses Docker-network values such as `postgres:5432`
- actual host Postgres listener is `5433`

Implication:

- Restarts can appear "successful" while still booting against the wrong env model.
- If the browser cannot reach the intended API base, the page shows a generic fetch failure.
- This affects old and new tasks equally because the fault is below task data.

## Non-Negotiable Rules

### 1. Do not mix container hostnames with host-run processes

These values are for containers on the Docker network, not for host-run Python:

- `postgres:5432`
- `deepwiki:8001`
- `zoekt:6070`
- `/data/repos`

If backend runs on the host, it must not rely on repo-root `.env` as-is.
Host-run repository clones should live under the repo-local `.repos/` directory unless explicitly overridden.

### 2. Treat Compose mode and host mode as different runtime topologies

Compose mode:

- backend can use `postgres:5432`
- backend can resolve `deepwiki`, `zoekt`, `gitnexus`

Host mode:

- browser reaches frontend on `3005`
- browser reaches backend on `8000`
- backend must use host-reachable dependency endpoints
- database must use host port `5433`

### 3. Host-run backend must not depend on implicit `.env` loading

Because `Settings()` resolves `.env` relative to cwd, a backend started from `backend/` ignores repo-root `/.env`.

Until config loading is hardened in code, use an explicit host-run env source.

Minimum required host-run DB value:

```bash
export DATABASE_URL='postgresql+asyncpg://codetalks:changeme@localhost:5433/codetalks'
```

Recommended host-run repo storage:

```bash
export REPOS_BASE_PATH="$(pwd)/.repos"
```

### 4. Browser-facing API URL must be reachable from the browser

`NEXT_PUBLIC_API_URL` is a browser contract, not a backend-internal setting.

Valid examples:

- `http://localhost:8000` when browsing locally on the same machine

Invalid examples:

- `http://postgres:5432`
- `http://deepwiki:8001`
- any container-only DNS name
- `localhost` from the wrong machine/browser context

### 5. Use listeners, not assumptions, to confirm runtime

Authoritative check:

```bash
lsof -nP -iTCP -sTCP:LISTEN | rg ':(3005|8000|5433|6070|7100|8001|8080|9090)\b'
```

This is the source of truth for "what is actually up".

## Restart Checklist

### Frontend

1. Confirm the old listener is gone from `3005`.
2. Start the frontend from `frontend/`.
3. Confirm `node` is listening on `3005`.

### Backend

1. Decide runtime mode first: host-run or Compose.
2. If host-run, export host-safe env vars before starting.
3. Start from `backend/`.
4. Confirm Python is listening on `8000`.
5. Do not assume repo-root `.env` was loaded.

### Database and tools

1. PostgreSQL host port must be `5433`.
2. Zoekt host port must be `6070`.
3. deepwiki-open host port must be `8001`.
4. GitNexus host port must be `7100`.

## Known Pitfalls

### Pitfall A: backend boots from `backend/`, but `.env` lives at repo root

Symptom:

- restart behavior is inconsistent
- one restart works only because someone exported env vars manually

Fix direction:

- either load repo-root `.env` explicitly in code
- or introduce a dedicated host-run env file / start script

### Pitfall B: repo-root `.env` is valid for Docker, not for host-run Python

Symptom:

- values look correct at a glance
- host-run backend still cannot use them safely

Reason:

- `postgres` is a container DNS name
- host uses `localhost:5433`

### Pitfall D: inline `base_url="http://deepwiki:8001"` bypasses settings entirely

Symptom:

- backend starts, `/api/tasks` works, but `/api/tasks/{id}/wiki` or chat returns 500
- backend log shows `httpx.ConnectError: nodename nor servname provided`

Root cause:

- Code constructs `httpx.AsyncClient(base_url="http://deepwiki:8001", ...)` inline,
  hard-coding the Docker hostname instead of reading `settings.deepwiki_base_url`.
- `config.py` and `backend/.env.local` are irrelevant — the URL is never even read from settings.

Fix and rule:

- Every `httpx.AsyncClient` that calls deepwiki, zoekt, or gitnexus **must** use
  `settings.deepwiki_base_url`, `settings.zoekt_base_url`, or `settings.gitnexus_base_url`.
- Never hard-code `http://deepwiki:*`, `http://zoekt:*`, or `http://gitnexus:*` in application code.

### Pitfall C: "service is up" is not the same as "browser can fetch it"

Symptom:

- process listens on a port
- frontend still shows fetch failure

Reason:

- the browser only cares whether `NEXT_PUBLIC_API_URL` resolves from its own context

## Recommended Hardening Follow-Up

1. Harden `backend/app/config.py` so host-run backend can reliably load repo-root `.env` or an explicit host-run env file.
2. Add a checked-in local start script that exports the host-safe env set before launching backend.
3. Keep this document as the single runtime truth source for local ports and restart rules.
