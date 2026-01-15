#!/usr/bin/env python3

import subprocess
import sys
import os
import argparse

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Create and download a .NET dump from a Kubernetes pod"
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
    "--dump-dir",
    default="/dotnetdumps",
    help="Directory in pod to store dumps in the container (default: /dotnetdumps)",
)

args = parser.parse_args()

# Local args
kube_ns = args.namespace
kube_pod = None

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

# Remote args
dump_type = args.dump_type
dump_pid = args.dump_pid
dump_dir = args.dump_dir

# validate if pod exists
try:
    subprocess.run(
        ["kubectl", "get", "pod", "-n", kube_ns, kube_pod],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except subprocess.CalledProcessError:
    print(
        f"Error: Pod {kube_pod} in namespace {kube_ns} does not exist.", file=sys.stderr
    )
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

# Get the real path of the symlink
print("Getting real path of latest_dump...")
try:
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            kube_ns,
            kube_pod,
            "--",
            "readlink",
            "-f",
            f"{dump_dir}/latest_dump",
        ],
        capture_output=True,
        text=True,
        check=True,
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
        check=True,
    )
    print(f"Successfully copied {filename}")
except subprocess.CalledProcessError as e:
    print(f"Error: Failed to copy file: {e.stderr}")
    sys.exit(1)
