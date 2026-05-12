# Local Folder Analysis — Docker Mode Configuration

This guide explains how to configure CodeTalks to analyze **local directories** (not GitHub repos)
when running in Docker Compose mode.

## Problem

When a user enters a local path (e.g. `D:\projects\myapp`) instead of a GitHub URL,
CodeTalks calls deepwiki's `/local_repo/structure` endpoint with that path.
By default, the deepwiki container cannot see the host's filesystem, so the path is
unreachable and a 404 error is returned.

## Solution: Mount the Local Repos Directory

You need to tell Docker Compose which host directory to mount into the containers.

### Step 1 — Set `LOCAL_REPOS_HOST_PATH` in `.env`

Open your `.env` file (copy from `.env.example` if you haven't yet) and add:

**Windows host:**
```env
LOCAL_REPOS_HOST_PATH=D:\projects
```

**Linux / macOS host:**
```env
LOCAL_REPOS_HOST_PATH=/home/yourname/projects
```

> Set this to the **parent directory** that contains your local repos,
> not to a specific repo. All subdirectories become accessible.

### Step 2 — Restart the containers

```bash
docker compose down
docker compose up -d
```

Docker Compose picks up `LOCAL_REPOS_HOST_PATH` from `.env` automatically and mounts it
at `/local_repos` inside both the `backend` and `deepwiki` containers.

### Step 3 — Analyze a local folder

In the CodeTalks UI, enter the full local path to the repo you want to analyze.
The path must be **under** `LOCAL_REPOS_HOST_PATH`:

| `LOCAL_REPOS_HOST_PATH` | Analyzable paths |
|---|---|
| `D:\projects` | `D:\projects\myapp`, `D:\projects\another-repo` |
| `/home/user/work` | `/home/user/work/service-a`, `/home/user/work/service-b` |

## What happens without configuration

If `LOCAL_REPOS_HOST_PATH` is not set and a user enters a local path, the backend returns
a clear error:

```
Directory not found in deepwiki container: /some/path.
In Docker mode, set LOCAL_REPOS_HOST_PATH in .env to the host directory
containing your local repos, then restart containers.
See docs/LOCAL_DEPLOYMENT.md for details.
```

## Host-run (non-Docker) mode

No extra configuration needed. The backend reads the host filesystem directly,
so any path that exists on the host is accessible. `LOCAL_REPOS_HOST_PATH` is ignored
when not running in Docker.

## Environment variables reference

| Variable | Default | Description |
|---|---|---|
| `LOCAL_REPOS_HOST_PATH` | _(empty)_ | Host directory to mount for local repo analysis |
| `LOCAL_REPOS_CONTAINER_PATH` | `/local_repos` | Container mount point (rarely needs changing) |
| `REPOS_BASE_PATH` | _(auto)_ | Host path to the managed `.repos` clone directory |

## Troubleshooting

**Still getting 404 after setting `LOCAL_REPOS_HOST_PATH`?**

1. Confirm the variable is in `.env` (not only in `.env.local`).
2. Run `docker compose config | grep local_repos` to verify the variable is expanded.
3. Check the mount is present: `docker exec codetalk-backend-1 ls /local_repos`
4. Ensure the path you analyze starts with `LOCAL_REPOS_HOST_PATH` exactly.

**Path not matching on Windows?**

Use backslashes in `.env`: `LOCAL_REPOS_HOST_PATH=D:\projects`
(Docker Desktop for Windows translates this correctly.)
