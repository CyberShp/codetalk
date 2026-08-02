---
feature_ids: [deployment]
topics: [deployment, web, frontend, backend]
doc_kind: operations-guide
created: 2026-08-01
---

# CodeTalk Web Deployment

The current release installs and manages only two services: `backend` and
`frontend`. GitNexus, CGC, Wiki, desktop packaging, updater, and rollback are
outside the deployer lifecycle.

## One-click Start

On Windows, run from the repository:

```powershell
cd deployer
.\start.bat
```

The launcher checks Python, Node.js, and Git; installs backend and frontend
dependencies; builds the frontend; starts both services; and opens:

```text
http://127.0.0.1:9000/start
```

Users do not choose a workspace, service set, or port during deployment.

## Port Policy

| Service | Preferred port |
|---|---:|
| frontend | 3003 |
| backend | 3004 |
| deployer manager | 9000 |

Before starting frontend or backend, the deployer attempts to stop the process
that owns the preferred port. If the operating system refuses the takeover or
the port remains occupied, it selects the next available port automatically.
When the backend port changes, frontend API configuration is regenerated before
the frontend starts.

The selected ports are runtime facts shown on the start page. They are not user
configuration fields.

## Start Page

The start page provides health, actual port, recent logs, and start, stop, or
restart controls for backend and frontend. `Start All` operates on those two
services only.

## Runtime Constraints

- Use one backend process for a shared `DATA_DIR`; do not add multiple Uvicorn
  workers until database-backed coordination is implemented.
- Development and test runtimes use Redis `6398` when Redis is needed. Never
  connect this project to Cat Cafe Redis `6399`.
- The public local defaults are frontend `3003` and backend `3004`.

## Troubleshooting

1. Open the start page and check the actual ports, health, and recent logs.
2. If a preferred port could not be reclaimed, confirm that both services use
   the automatically selected port pair shown on the page.
3. If dependency installation fails, verify Python, Node.js, npm, and Git are
   available to the same shell that launched `start.bat`.
4. If frontend cannot reach backend, restart both services from the start page
   so frontend configuration is regenerated from the active backend port.

Windows EXE packaging, application upgrades, and application rollback remain
deferred until the Web release is stable in the intranet environment.
