#!/usr/bin/env python3

import subprocess
import sys
import os

# Local args
kube_ns = "energy-promv4"
kube_pod = "api-7798c4bdf7-68sw9"

# Remote args
dump_type = "mini"
dump_pid = "1"
dump_dir = "/dotnetdumps"

# validate if pod exists
try:
    subprocess.run(
        ["kubectl", "get", "pod", "-n", kube_ns, kube_pod],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
except subprocess.CalledProcessError:
    print(f"Error: Pod {kube_pod} in namespace {kube_ns} does not exist.", file=sys.stderr)
    sys.exit(1)

# Read remote.sh script
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

{remote_script}
"""

# Execute the script in the container
print(f"Executing script in pod {kube_pod} (namespace: {kube_ns})...")
try:
    result = subprocess.run(
        ["kubectl", "exec", "-n", kube_ns, "-i", kube_pod, "--", "bash"],
        input=script_content,
        text=True
    )
    if result.returncode != 0:
        print(f"Error: kubectl exec failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
except FileNotFoundError:
    print("Error: kubectl not found. Please install kubectl.", file=sys.stderr)
    sys.exit(1)

# Get the real path of the symlink
print("Getting real path of latest_dump...")
try:
    result = subprocess.run(
        ["kubectl", "exec", "-n", kube_ns, kube_pod, "--", "readlink", "-f", f"{dump_dir}/latest_dump"],
        capture_output=True,
        text=True,
        check=True
    )
    real_path = result.stdout.strip()
    print(f"Real path: {real_path}")
except subprocess.CalledProcessError as e:
    print(f"Error: Failed to get real path: {e.stderr}", file=sys.stderr)
    sys.exit(1)

# Get the filename only for local file
filename = os.path.basename(real_path)

# Copy the actual file
print(f"Copying {filename} from pod to local directory...")
try:
    subprocess.run(
        ["kubectl", "cp", f"{kube_ns}/{kube_pod}:{real_path}", f"./{filename}"],
        check=True
    )
    print(f"Successfully copied {filename}")
except subprocess.CalledProcessError as e:
    print(f"Error: Failed to copy file: {e.stderr}")
    sys.exit(1)
