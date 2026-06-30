# Local Folder Analysis

This guide explains how CodeTalk analyzes local source directories in the
current runtime.

## Current Runtime

CodeTalk no longer deploys or manages the legacy Wiki component. Local folder analysis is handled
by the CodeTalk backend, GitNexus, AI threads, and Workbench workflows.

Default local ports:

| Service | Default |
|---|---:|
| Frontend | 3003 |
| Backend API | 3004 |
| GitNexus | 7100 |
| CGC optional enhancer | 7072 |

## Host-Run Mode

No extra mount configuration is required. The backend reads paths that exist on
the host.

Use the exact local path when creating a workspace, for example:

```text
/Volumes/Media/dpdk/spdk
```

If the path is wrong, the UI should report that the directory does not exist.

## Deployer Mode

Use `deployer/start.sh` or `deployer/start.bat`, then choose native deployment.
The deployer writes:

- `backend/.env`
- `frontend/.env.local`
- the deployer local config file

GitNexus and CGC are installed under the configured workspace directory. CGC is
optional: if its install/startup fails, CodeTalk should still start
backend/frontend/GitNexus and show a clear warning in the deploy log.

## Troubleshooting

**Backend or frontend port conflict**

Change the ports in the deployer advanced settings, or use force takeover when
you are sure the running process belongs to the previous local deployment.

**CGC install or startup failure**

Core CodeTalk can still run. Fix CGC only when you need symbol graph or call-chain
enhancement. Check the deploy log for the exact Python environment or wheelhouse
error.

**Local path not found**

Confirm the path exists on the same machine that runs the backend. In native
deployment, no container path translation is needed.
