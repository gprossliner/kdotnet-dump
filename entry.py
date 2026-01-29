#!/usr/bin/env python3

import subprocess
import sys
import os
import argparse
import json

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Create and download a .NET dump from a Kubernetes pod"
)

# add a "strategy" argument:
#   --strategy same-container: install dotnet-dump in the same container as the app, requires only `exec`, but root container
#   --strategy debug-container: install use a ephemeral debug container to run dotnet-dump, requires RBAC, but works with non-root containers
parser.add_argument(
    "--strategy",
    choices=["same-container", "debug-container"],
    default="debug-container",
    help="Strategy to create the dump (default: debug-container)",
)

parser.add_argument("pod", nargs="?", help="Pod name")
parser.add_argument(
    "-n", "--namespace", default="default", help="Namespace (default: default)"
)
parser.add_argument(
    "-l", "--selector", help="Label selector to find pod (e.g., app=myapp)"
)
parser.add_argument(
    "--dump-type",
    default="mini",
    choices=["mini", "heap", "triage", "full"],
    help="Dump type (default: mini)",
)
parser.add_argument("--dump-pid", default="1", help="Process ID to dump (default: 1)")

parser.add_argument(
    "--debug-image", help="Debug container image to use (default mcr.microsoft.com/dotnet/sdk:latest)",
    default="mcr.microsoft.com/dotnet/sdk:latest",
)

args = parser.parse_args()

# Local args
kube_ns = args.namespace
kube_pod = None
container_name = None

# Determine pod name
if args.selector:
    # Get pod by label selector
    print(f"Finding pod with selector '{args.selector}' in namespace '{kube_ns}'...")
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                kube_ns,
                "-l",
                args.selector,
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        kube_pod = result.stdout.strip()
        if not kube_pod:
            print(
                f"Error: No pods found with selector '{args.selector}' in namespace '{kube_ns}'",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Found pod: {kube_pod}")
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to find pod with selector: {e.stderr}", file=sys.stderr)
        sys.exit(1)
elif args.pod:
    kube_pod = args.pod
else:
    print(
        "Error: Either pod name or --selector (-l) must be specified", file=sys.stderr
    )
    parser.print_help()
    sys.exit(1)

# validate if pod exists and get pod details
try:
    result = subprocess.run(
        ["kubectl", "get", "pod", "-n", kube_ns, kube_pod, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    pod_data = json.loads(result.stdout)
except subprocess.CalledProcessError:
    print(
        f"Error: Pod {kube_pod} in namespace {kube_ns} does not exist.", file=sys.stderr
    )
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"Error: Failed to parse pod data: {e}", file=sys.stderr)
    sys.exit(1)

# Extract default container name
containers = pod_data.get("spec", {}).get("containers", [])
if not containers:
    print(f"Error: No containers found in pod {kube_pod}", file=sys.stderr)
    sys.exit(1)

# Default container is the first one, or one with specific annotation
default_container = containers[0].get("name")
annotations = pod_data.get("metadata", {}).get("annotations", {})
if "kubectl.kubernetes.io/default-container" in annotations:
    default_container = annotations["kubectl.kubernetes.io/default-container"]


if container_name is None:
    print(f"Using default container: {default_container}")
    container_name = default_container

# Extract UID/GID from status.containerStatuses (actual runtime values)
container_statuses = pod_data.get("status", {}).get("containerStatuses", [])
uid = None
gid = None

# Find the container status for the default container
for container_status in container_statuses:
    if container_status.get("name") == container_name:
        # Get user info from container status
        user_info = container_status.get("user", {})
        if "linux" in user_info:
            uid = user_info["linux"].get("uid")
            gid = user_info["linux"].get("gid")
        break

# Fallback to spec if status doesn't have the info (old k8s versions)
if uid is None or gid is None:
    container_spec = None
    for container in containers:
        if container.get("name") == container_name:
            container_spec = container
            break
    
    if container_spec:
        security_context = container_spec.get("securityContext", {})
        pod_security_context = pod_data.get("spec", {}).get("securityContext", {})
        
        if uid is None:
            uid = security_context.get("runAsUser") or pod_security_context.get("runAsUser")
        if gid is None:
            gid = security_context.get("runAsGroup") or pod_security_context.get("runAsGroup")

if uid:
    print(f"Container runs as UID: {uid}")
if gid:
    print(f"Container runs as GID: {gid}")
if not uid and not gid:
    print("Container runs as root or UID/GID not explicitly set")

# prepare script
################################################################

# args
dump_type = args.dump_type
dump_pid = args.dump_pid
strategy = args.strategy

if strategy == "same-container":
    dump_dir = "/tmp/dumps"
elif strategy == "debug-container":
    dump_dir = f"/proc/{dump_pid}/root/tmp/dumps"

try:
    with open("remote.sh", "r") as f:
        remote_script = f.read()
except FileNotFoundError:
    print("Error: remote.sh not found", file=sys.stderr)
    sys.exit(1)

# Prepare the script to send to the container
script_content = f"""dump_type="{dump_type}"
dump_pid="{dump_pid}"
dump_dir="{dump_dir}"
strategy="{strategy}"

{remote_script}
"""

# Execute the script in the container
if strategy == "same-container":
    print(f"Executing script in pod {kube_pod} (namespace: {kube_ns})...")
    try:
        result = subprocess.run(
            ["kubectl", "exec", "-n", kube_ns, "-i", kube_pod, "--", "bash"],
            input=script_content,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Error: kubectl exec failed with exit code {result.returncode}",
                file=sys.stderr,
            )
            sys.exit(result.returncode)
    except FileNotFoundError:
        print("Error: kubectl not found. Please install kubectl.", file=sys.stderr)
        sys.exit(1)

elif strategy == "debug-container":
    print(f"Creating debug container using image {args.debug_image}...")
    
    # Build kubectl debug command
    debug_cmd = [
        "kubectl",
        "debug",
        "-n",
        kube_ns,
        kube_pod,
        f"--image={args.debug_image}",
        f"--target={container_name}",
        "--share-processes",
        "-i",
    ]
    
    # Add custom security context if UID/GID is set
    custom_file = None
    if uid is not None or gid is not None:
        custom_spec = { 
            "securityContext": {
                "runAsUser": uid,
                "runAsGroup": gid
            }
        }

        # Write custom spec to temp file
        import tempfile
        custom_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(custom_spec, custom_file)
        custom_file.close()
        
        debug_cmd.extend(["--custom", custom_file.name])
        print(f"Debug container will run as UID={uid}, GID={gid}")
    
    debug_cmd.extend(["--", "bash"])
    
    try:
        # kubectl debug doesn't stream output well with input=, so use stdin pipe
        process = subprocess.Popen(
            debug_cmd,
            stdin=subprocess.PIPE,
            text=True,
        )
        # Send script and close stdin to trigger execution
        process.communicate(input=script_content)
        
        if process.returncode != 0:
            print(
                f"Error: kubectl debug failed with exit code {process.returncode}",
                file=sys.stderr,
            )
            sys.exit(process.returncode)
    except FileNotFoundError:
        print("Error: kubectl not found. Please install kubectl.", file=sys.stderr)
        sys.exit(1)
    finally:
        # Clean up temp file
        if custom_file:
            os.unlink(custom_file.name)

dump_file = dump_dir + "/latest_dump"
local_file = "./latest_dump"
# Copy the actual file
print(f"Copying {dump_file} from pod to local directory...")
try:
    subprocess.run(
        ["kubectl", "cp", "--container", container_name, f"{kube_ns}/{kube_pod}:{dump_file}", local_file],
        check=True,
    )
    print(f"Successfully copied {os.path.basename(dump_file)} to {local_file}")
except subprocess.CalledProcessError as e:
    print(f"Error: Failed to copy file: {e.stderr}")
    sys.exit(1)
