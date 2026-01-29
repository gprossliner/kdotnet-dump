#!/usr/bin/env python3

import subprocess
import sys
import os
import argparse
import json
import time

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

# create a list of existing ephemeralContainers names
existing_ephemeral_containers = []
ephemeral_containers = pod_data.get("spec", {}).get("ephemeralContainers", [])
for ec in ephemeral_containers:
    existing_ephemeral_containers.append(ec.get("name"))

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
        debug_cmd.extend(["--profile", "restricted"])
        debug_cmd.extend(["--custom", custom_file.name])
        print(f"Debug container will run as UID={uid}, GID={gid}")
    
    debug_cmd.extend(["--", "bash"])
    
    try:
        # kubectl debug doesn't stream output well with input=, so use stdin pipe
        process = subprocess.Popen(
            debug_cmd,
            stdin=subprocess.PIPE,
            stdout=sys.stdout,
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

    # get the name of the ephemeral debug container
    debug_container_name = None

    while True:
        print("Waiting for debug container to complete...")
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

        ephemeral_containers = pod_data.get("status", {}).get("ephemeralContainerStatuses", [])
        has_terminated = False
        for ec in ephemeral_containers:
            name = ec.get("name")
            if name not in existing_ephemeral_containers:
                # check if terminated
                state = ec.get("state", {})
                if "terminated" in state:
                    print(f"Debug container '{name}' has terminated.")
                    has_terminated = True
                    break
            else:
                continue
        if has_terminated:
            break
        else:
            # no new debug container found yet
            time.sleep(1)
            continue
        

dump_file = dump_dir + "/latest_dump"
local_file = "./latest_dump"

# delete the local file if exist
if os.path.exists(local_file):
    os.remove(local_file)


def kubectl_cp(namespace, pod, container, remote_file, local_file):
    """Copy file from pod using kubectl cp (may fail with large files >350MB)"""
    print(f"Copying {remote_file} using kubectl cp...")
    try:
        subprocess.run(
            ["kubectl", "cp", "--container", container, f"{namespace}/{pod}:{remote_file}", local_file],
            check=True,
        )
        print(f"Successfully copied to {local_file}")
    except subprocess.CalledProcessError as e:
        print(f"Error: kubectl cp failed: {e}", file=sys.stderr)
        sys.exit(1)


def kubectl_tar_cp(namespace, pod, container, remote_file, local_file):
    """Copy file from pod using tar streaming (reliable for large files)"""
    print(f"Copying {remote_file} using tar streaming...")
    try:
        # Extract directory and filename
        remote_dir = os.path.dirname(remote_file)
        remote_filename = os.path.basename(remote_file)
        
        # Use tar streaming: kubectl exec -- tar cf - file | tar xf -
        kubectl_cmd = [
            "kubectl", "exec", "-n", namespace, pod,
            "--container", container,
            "--", "tar", "cf", "-", "-C", remote_dir, remote_filename
        ]
        
        tar_extract = subprocess.Popen(
            ["tar", "xf", "-"],
            stdin=subprocess.PIPE,
            cwd=".",
        )
        
        kubectl_proc = subprocess.Popen(
            kubectl_cmd,
            stdout=tar_extract.stdin,
        )
        
        # Close tar's stdin (kubectl will write to it)
        tar_extract.stdin.close()
        
        # Wait for both processes
        kubectl_rc = kubectl_proc.wait()
        tar_rc = tar_extract.wait()
        
        if kubectl_rc != 0:
            print(f"Error: kubectl exec failed with exit code {kubectl_rc}", file=sys.stderr)
            sys.exit(kubectl_rc)
        if tar_rc != 0:
            print(f"Error: tar extraction failed with exit code {tar_rc}", file=sys.stderr)
            sys.exit(tar_rc)
        
        # Rename extracted file to local_file
        if os.path.exists(remote_filename):
            os.rename(remote_filename, local_file)
            print(f"Successfully copied to {local_file}")
        else:
            print(f"Error: Expected file {remote_filename} not found after extraction", file=sys.stderr)
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: Failed to copy file: {e}", file=sys.stderr)
        sys.exit(1)


def kubectl_chunked_cp(namespace, pod, container, remote_file, local_file, chunk_size=10*1024*1024):
    """Copy file from pod in chunks using base64 encoding to avoid websocket issues"""
    print(f"Copying {remote_file} using chunked transfer (chunk size: {chunk_size//1024//1024}MB)...")
    
    try:
        # Get file size first
        size_cmd = [
            "kubectl", "exec", "-n", namespace, pod,
            "--container", container,
            "--", "sh", "-c", f"stat -c %s '{remote_file}' 2>/dev/null || stat -f %z '{remote_file}'"
        ]
        result = subprocess.run(size_cmd, capture_output=True, text=True, check=True)
        total_size = int(result.stdout.strip())
        print(f"Remote file size: {total_size} bytes ({total_size/1024/1024:.2f} MB)")
        
        # Open local file for writing
        with open(local_file, 'wb') as f:
            offset = 0
            chunk_num = 0
            
            while offset < total_size:
                chunk_num += 1
                bytes_to_read = min(chunk_size, total_size - offset)
                print(f"Downloading chunk {chunk_num} (offset {offset}, size {bytes_to_read} bytes)...")
                
                # Read chunk using dd and base64 encode it to avoid binary issues over websocket
                read_cmd = [
                    "kubectl", "exec", "-n", namespace, pod,
                    "--container", container,
                    "--", "sh", "-c",
                    f"dd if='{remote_file}' bs={bytes_to_read} skip=0 count=1 iflag=skip_bytes,count_bytes 2>/dev/null | base64"
                ]
                
                # Update dd skip parameter
                read_cmd[-1] = f"dd if='{remote_file}' bs=1M skip={offset} count={bytes_to_read} iflag=skip_bytes,count_bytes 2>/dev/null | base64"
                
                result = subprocess.run(read_cmd, capture_output=True, text=True, check=True)
                
                # Decode base64 and write to file
                import base64
                chunk_data = base64.b64decode(result.stdout)
                f.write(chunk_data)
                
                offset += len(chunk_data)
                print(f"Progress: {offset}/{total_size} bytes ({100*offset//total_size}%)")
        
        # Verify file size
        local_size = os.path.getsize(local_file)
        if local_size == total_size:
            print(f"Successfully copied to {local_file} ({local_size} bytes)")
        else:
            print(f"Warning: Size mismatch - expected {total_size}, got {local_size}", file=sys.stderr)
            
    except subprocess.CalledProcessError as e:
        print(f"Error: Command failed: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to copy file: {e}", file=sys.stderr)
        sys.exit(1)



# Copy the file from pod
# Choose one of: kubectl_cp, kubectl_tar_cp, kubectl_chunked_cp
kubectl_chunked_cp(kube_ns, kube_pod, container_name, dump_file, local_file)

