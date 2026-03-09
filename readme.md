# kdotnet-dump

A tool to create and download .NET process dumps from Kubernetes pods, supporting both standard and hardened (non-root) containers.

## Purpose

Creating .NET dumps from containers running in Kubernetes can be challenging, especially with:
- **Non-root containers** - Can't install tools or write to most filesystem locations
- **Chiseled/distroless-style runtime containers** - Missing package managers and common shell tooling
- **Restricted Pod Security Standards** - Limited privileges and capabilities
- **Large dump files** - Can exceed 350MB, causing kubectl cp to fail

`kdotnet-dump` solves these problems by:
1. Using ephemeral debug containers to isolate diagnostic tooling from application containers
2. Automatically detecting container UID/GID and matching security context
3. Automatically detecting and downloading `dotnet-dump` in the debug container (no need to bake it into app images)
4. Use a ephemeral debug container to download if the debuggee container doesn't
contain the required tools or a shell. This is automatically dedected.
5. Using chunked base64 transfer to handle large files reliably
6. Working with `/proc/1/root/tmp` to share filesystem between debug and target containers

## Installation

The tool is published to [kdotnet-dump · PyPI](https://pypi.org/project/kdotnet-dump/),
which is the prefered method of installation:

```bash
pip install kdotnet-dump
```

If you want to install from local sources (e.g. for debugging), you can also use

```bash
git clone https://github.com/gprossliner/kdotnet-dump.git
cd kdotnet-dump
pip install -e .
```

No additional dependencies required - just Python 3 and `kubectl`.

## Command Line Arguments

```
usage: kdotnet-dump [-h] [--strategy {same-container,debug-container}]
                [-n NAMESPACE] [-l SELECTOR] [--dump-type {mini,heap,triage,full}]
                [--dump-pid DUMP_PID] [--debug-image DEBUG_IMAGE] [-v]
                [pod]

positional arguments:
  pod                   Pod name (or use --selector)

optional arguments:
  -h, --help            Show this help message and exit
  -v, --version         Show kdotnet-dump version and exit
  
  --strategy {same-container,debug-container}
                        Strategy to create the dump (default: debug-container)
                        - same-container: Runs dotnet-dump in the target container (requires root)
                        - debug-container: Uses ephemeral debug container (works with non-root)
  
  -n, --namespace NAMESPACE
                        Kubernetes namespace (default: default)
  
  -l, --selector SELECTOR
                        Label selector to find pod (e.g., app=myapp)
                        Use this instead of specifying pod name directly
  
  --dump-type {mini,heap,triage,full}
                        Dump type (default: mini)
                        - mini: Minimal dump with stacks and exception info
                        - heap: Includes heap memory
                        - triage: Minimal info for initial diagnosis
                        - full: Complete memory dump (can be very large)
  
  --dump-pid DUMP_PID   Process ID to dump (default: 1)
                        Usually PID 1 is the main application process
  
  --debug-image DEBUG_IMAGE
                        Debug container image to use
                        (default: mcr.microsoft.com/dotnet/sdk:latest)
```

When installed from PyPI/release artifacts, `-v/--version` prints the package version
(for example `v0.1.3`).
When run directly from source without an installed package, it prints `unknown`.

## Examples

### Basic Usage - Find pod by label selector

```bash
kdotnet-dump -n production -l app=api --dump-type mini
```

This will:
1. Find a pod with label `app=api` in namespace `production`
2. Create an ephemeral debug container
3. Download and run dotnet-dump to create a mini dump
4. Download the dump to `./latest_dump`

### Specify pod name directly

```bash
kdotnet-dump -n production my-app-pod-12345 --dump-type full
```

### Create full heap dump for memory analysis

```bash
kdotnet-dump -l app=api --dump-type heap
```

### Use custom debug image (e.g., specific .NET SDK version)

```bash
kdotnet-dump -l app=api --debug-image mcr.microsoft.com/dotnet/sdk:8.0
```

### Use same-container strategy (requires root)

```bash
kdotnet-dump -l app=api --strategy same-container --dump-type mini
```

## Analyzing Dumps

### On Linux

```bash
dotnet tool install -g dotnet-dump
dotnet-dump analyze ./latest_dump
```

### On macOS (using Docker)

macOS doesn't support analyzing Linux dumps natively. Use a Docker container with the correct platform.

If you got the dump from a locally running kind cluster in MacOS Docker Desktop,
the platform should be `linux/arm64`.

If you got the dump from a kubernetes cluster running on Linux, you may use the
platform `linux/amd64`.

If you are not sure, you can use the `sosstatus` command to verify the platform
of the dump.

```bash
# For Apple Silicon (M1/M2/M3), specify linux/arm64 platform
# This is the correct choice if you run locally on MacOS with a kind cluster
# For a different environment you may need to change the platform
docker run --rm -it --platform linux/arm64 \
  -v $(pwd):/dumps \
  mcr.microsoft.com/dotnet/sdk:10.0 \
  bash

# Inside the container
dotnet tool install dotnet-dump
dotnet tool run dotnet-dump analyze /dumps/latest_dump
```

Common dotnet-dump commands:
```
clrthreads          # List managed threads
clrstack            # Show managed stack trace
dumpheap -stat      # Show heap statistics
sos help            # Show all available commands
```

## How It Works

### Debug Container Strategy (Recommended)

1. **Pod Discovery**: Finds target pod using label selector or name
2. **Container Detection**: Identifies the default container and extracts UID/GID
3. **Security Context Matching**: Creates debug container with same UID/GID as target
4. **Shared Process Namespace**: Uses `--share-processes` to access target container's processes
5. **Dump Creation**: Downloads dotnet-dump and creates dump in `/proc/1/root/tmp/dumps`
6. **File Transfer**: Uses chunked base64 encoding to reliably download large files

### Same Container Strategy

Simpler but requires:
- Container running as root
- Writable `/tmp` directory
- Works by installing dotnet-dump directly in the target container

## Requirements

### Kubernetes Cluster
- Kubernetes 1.23+ (for ephemeral containers)
- Kubernetes 1.28+ recommended (for UID/GID detection from status)

### RBAC Permissions
For debug-container strategy, the user needs:
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ephemeral-container-access
  labels:
    # This label causes the ClusterRole to be aggregated to the edit / admin role
    rbac.authorization.k8s.io/aggregate-to-edit: "true"
    rbac.authorization.k8s.io/aggregate-to-admin: "true"
rules:
- apiGroups: [""]
  resources: ["pods/ephemeralcontainers"]
  verbs: ["patch", "create", "get"]
```

### Target Container Requirements
- .NET application running (tested with .NET 8.0, 9.0, 10.0)
- Writable `/tmp` directory OR mounted emptyDir volume
  - For `readOnlyRootFilesystem: true`, mount emptyDir at `/tmp`

## Troubleshooting

### "Access to the path '/.dotnet' is denied"
This occurs in non-root containers. Use `--strategy debug-container` (default).

### "Read-only file system" errors
For containers with `readOnlyRootFilesystem: true`, ensure `/tmp` has an emptyDir mount:
```yaml
volumeMounts:
- name: tmp
  mountPath: /tmp
volumes:
- name: tmp
  emptyDir: {}
```

### Large files fail with websocket errors
The tool automatically uses chunked transfer for reliability. If issues persist, the chunks are 10MB by default and can't be adjusted without code changes.

### "No pods found with selector"
Verify the label selector matches your pods:
```bash
kubectl get pods -l app=myapp -n your-namespace
```

## Testing

Integration tests are available in the `tests/` directory:

```bash
pip install -r tests/requirements.txt
pytest tests/test_entry.py -v
```

Tests deploy sample applications and verify dump creation across different configurations:
- Root vs non-root containers
- Debian vs Alpine vs Chiseled images
- With and without `readOnlyRootFilesystem`

## License

MIT
