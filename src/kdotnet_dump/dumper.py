#!/usr/bin/env python3

import subprocess
import sys
import os
import json
import time


class Dumper:
    def __init__(self, namespace="", pod=None, selector=None, dump_type="mini", dump_pid="1", strategy="debug-container", debug_image="mcr.microsoft.com/dotnet/sdk:latest", verbose_output=False):
        self.namespace = namespace
        self.pod = pod
        self.selector = selector
        self.dump_type = dump_type
        self.dump_pid = dump_pid
        self.strategy = strategy
        self.debug_image = debug_image
        self.verbose_output = verbose_output

    def _kubectl_command(self, args):
        command = ["kubectl", *args]
        if self.verbose_output:
            import shlex
            print(f"[kubectl] {shlex.join(command)}")
        return command

    def _kubectl_run(self, args, **kwargs):
        try:
            return subprocess.run(self._kubectl_command(args), **kwargs)
        except FileNotFoundError:
            print("Error: kubectl not found. Please install kubectl.", file=sys.stderr)
            sys.exit(1)

    def _kubectl_popen(self, args, **kwargs):
        try:
            return subprocess.Popen(self._kubectl_command(args), **kwargs)
        except FileNotFoundError:
            print("Error: kubectl not found. Please install kubectl.", file=sys.stderr)
            sys.exit(1)

    def run(self):
        # Local args
        container_name = None

        # Determine pod name
        if self.selector:
            # Get pod by label selector
            print(f"Finding pod with selector '{self.selector}' in namespace '{self.namespace}'...")
            try:
                result = self._kubectl_run(
                    [
                        "get",
                        "pods",
                        "-n",
                        self.namespace,
                        "-l",
                        self.selector,
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                pods_data = json.loads(result.stdout)
                items = pods_data.get("items", [])
                if not items:
                    print(
                        f"Error: No pods found with selector '{self.selector}' in namespace '{self.namespace}'",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                self.pod = items[0].get("metadata", {}).get("name")
                if not self.pod:
                    print(
                        f"Error: Pod list returned no valid pod name for selector '{self.selector}' in namespace '{self.namespace}'",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                print(f"Found pod: {self.pod}")
            except subprocess.CalledProcessError as e:
                print(f"Error: Failed to find pod with selector: {e.stderr}", file=sys.stderr)
                sys.exit(1)
            except json.JSONDecodeError as e:
                print(f"Error: Failed to parse pod list data: {e}", file=sys.stderr)
                sys.exit(1)
        elif self.pod:
            pass
        else:
            sys.exit(1)

        # validate if pod exists and get pod details
        try:
            result = self._kubectl_run(
                ["get", "pod", "-n", self.namespace, self.pod, "-o", "json"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.pod_data = PodData(json.loads(result.stdout))
        except subprocess.CalledProcessError:
            print(
                f"Error: Pod {self.pod} in namespace {self.namespace} does not exist.", file=sys.stderr
            )
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse pod data: {e}", file=sys.stderr)
            sys.exit(1)

        # Extract default container name
        default_container = self.pod_data.get_default_container()

        if container_name is None:
            print(f"Using default container: {default_container}")
            container_name = default_container

        # Extract UID/GID from status.containerStatuses (actual runtime values)
        uid, gid = self.pod_data.get_container_uid_gid(container_name)
        print(f"Container '{container_name}' UID/GID: {uid}/{gid}")

        # create a list of existing ephemeralContainers names
        existing_ephemeral_containers = self.pod_data.get_ephemeral_container_names()

        # prepare script
        ################################################################

        # args
        dump_type = self.dump_type
        dump_pid = self.dump_pid
        strategy = self.strategy
        dump_dir = f"/proc/{dump_pid}/root/tmp/dumps"

        # Find remote.sh - it should be in the same package directory
        package_dir = os.path.dirname(os.path.abspath(__file__))
        remote_sh_path = os.path.join(package_dir, "remote.sh")
        
        if not os.path.exists(remote_sh_path):
            print(f"Error: remote.sh not found at {remote_sh_path}", file=sys.stderr)
            sys.exit(1)

        try:
            with open(remote_sh_path, "r") as f:
                remote_script = f.read()
        except FileNotFoundError:
            print(f"Error: remote.sh not found at {remote_sh_path}", file=sys.stderr)
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
            print(f"Executing script in pod {self.pod} (namespace: {self.namespace})...")
            result = self._kubectl_run(
                ["exec", "-n", self.namespace, "-i", self.pod, "--", "sh"],
                input=script_content,
                text=True,
            )
            if result.returncode != 0:
                print(
                    f"Error: kubectl exec failed with exit code {result.returncode}",
                    file=sys.stderr,
                )
                sys.exit(result.returncode)

        elif strategy == "debug-container":
            print(f"Creating debug container using image {self.debug_image}...")
            
            # Build kubectl debug command
            debug_cmd = [
                "debug",
                "-n",
                self.namespace,
                self.pod,
                f"--image={self.debug_image}",
                f"--target={container_name}",
                "--share-processes",
                "-i",
            ]
            
            # Add custom security context if UID/GID is set
            custom_file = None
            if uid is not None and uid != 0:
                custom_file = self._debug_custom_file(uid, gid)
                debug_cmd.extend(["--profile", "restricted"])
                debug_cmd.extend(["--custom", custom_file.name])
                # print(f"Debug container will run as UID={uid}, GID={gid}")
            else:
                debug_cmd.extend(["--profile", "baseline"])

            debug_cmd.extend(["--", "bash"])
            
            try:
                # kubectl debug doesn't stream output well with input=, so use stdin pipe
                redir = subprocess.PIPE if not self.verbose_output else None
                process = self._kubectl_popen(
                    debug_cmd,
                    stdin=subprocess.PIPE,
                    stdout=redir,
                    stderr=redir,
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
            finally:
                # Clean up temp file
                if custom_file:
                    custom_file.close()

            print("Waiting for debug container to complete", end="")
            while True:
                print(".", end="", flush=True)
                try:
                    result = self._kubectl_run(
                        ["get", "pod", "-n", self.namespace, self.pod, "-o", "json"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.pod_data.refresh(json.loads(result.stdout))
                except subprocess.CalledProcessError:
                    print(
                        f"Error: Pod {self.pod} in namespace {self.namespace} does not exist.", file=sys.stderr
                    )
                    sys.exit(1)
                except json.JSONDecodeError as e:
                    print(f"Error: Failed to parse pod data: {e}", file=sys.stderr)
                    sys.exit(1)

                # get the new ephemeral name
                debug_container_name = list(set(self.pod_data.get_ephemeral_container_names()) - set(existing_ephemeral_containers))[0]
                if self.pod_data.has_ephemeral_container_terminated(debug_container_name):
                    print("")
                    print("Debug container has terminated, proceeding to copy dump file...")
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
                self._kubectl_run(
                    ["cp", "--container", container, f"{namespace}/{pod}:{remote_file}", local_file],
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
                    "exec", "-n", namespace, pod,
                    "--container", container,
                    "--", "tar", "cf", "-", "-C", remote_dir, remote_filename
                ]
                
                tar_extract = subprocess.Popen(
                    ["tar", "xf", "-"],
                    stdin=subprocess.PIPE,
                    cwd=".",
                )
                
                kubectl_proc = self._kubectl_popen(
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
                    "exec", "-n", namespace, pod,
                    "--container", container,
                    "--", "sh", "-c", f"stat -c %s '{remote_file}' 2>/dev/null || stat -f %z '{remote_file}'"
                ]
                result = self._kubectl_run(size_cmd, capture_output=True, text=True, check=True)
                total_size = int(result.stdout.strip())
                print(f"Remote file size: {total_size} bytes ({total_size/1024/1024:.2f} MB)")
                
                # get file md5sum for verification
                md5_cmd = [
                    "exec", "-n", namespace, pod,
                    "--container", container,
                    "--", "sh", "-c", f"md5sum '{remote_file}' 2>/dev/null || md5 -q '{remote_file}'"
                ]
                result = self._kubectl_run(md5_cmd, capture_output=True, text=True, check=True)
                remote_md5 = result.stdout.strip().split()[0]
                print(f"Remote file MD5: {remote_md5}")

                # Open local file for writing
                with open(local_file, 'wb') as f:
                    offset = 0
                    chunk_num = 0
                    
                    while offset < total_size:
                        chunk_num += 1
                        bytes_to_read = min(chunk_size, total_size - offset)
                        # print(f"Downloading chunk {chunk_num} (offset {offset}, size {bytes_to_read} bytes)...")
                        
                        # Read chunk using dd and base64 encode it to avoid binary issues over websocket
                        read_cmd = [
                            "exec", "-n", namespace, pod,
                            "--container", container,
                            "--", "sh", "-c",
                            f"dd if='{remote_file}' bs={bytes_to_read} skip=0 count=1 iflag=skip_bytes,count_bytes 2>/dev/null | base64"
                        ]
                        
                        # Update dd skip parameter
                        read_cmd[-1] = f"dd if='{remote_file}' bs=1M skip={offset} count={bytes_to_read} iflag=skip_bytes,count_bytes 2>/dev/null | base64"
                        
                        result = self._kubectl_run(read_cmd, capture_output=True, text=True, check=True)
                        
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
                    
                # Verify file md5 by not using md5 (not available on windows) but using python's hashlib
                import hashlib
                md5_hash = hashlib.md5()
                with open(local_file, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        md5_hash.update(chunk)
                local_md5 = md5_hash.hexdigest()
                if local_md5 == remote_md5:
                    print(f"MD5 verification successful: {local_md5}")
                else:                    
                    print(f"Warning: MD5 mismatch - expected {remote_md5}, got {local_md5}", file=sys.stderr)
            except subprocess.CalledProcessError as e:
                print(f"Error: Command failed: {e}", file=sys.stderr)
                print(f"stderr: {e.stderr}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"Error: Failed to copy file: {e}", file=sys.stderr)
                sys.exit(1)

        # test if required tools are available for copying in the debuggee container
        copy_container = container_name
        dw_process = None

        if not self._check_container_tools(self.namespace, self.pod, container_name, ["tar"]):
            print("Required tools for file transfer are not available in the container.", file=sys.stderr)
            print("Start debug container for file transfer.", file=sys.stderr)
          
            # start debug container for file transfer
            e_containers = self.pod_data.get_ephemeral_container_names()
            # Build kubectl debug command
            debug_cmd = [
                "debug",
                "-n",
                self.namespace,
                self.pod,
                f"--image={self.debug_image}",
                f"--target={container_name}",
                "--share-processes"
            ]
            
            # Add custom security context if UID/GID is set
            custom_file = None
            if uid is not None and uid != 0:
                custom_file = self._debug_custom_file(uid, gid)
                debug_cmd.extend(["--profile", "restricted"])
                debug_cmd.extend(["--custom", custom_file.name])
                # print(f"Debug container will run as UID={uid}, GID={gid}")
            else:
                debug_cmd.extend(["--profile", "baseline"])

            debug_cmd.extend(["--", "sh", "-c", "while [ ! -f /tmp/stopfile ]; do sleep 1; done"])  # Keep debug container running for file transfer
            
            try:
                # kubectl debug doesn't stream output well with input=, so use stdin pipe
                redir = subprocess.PIPE if not self.verbose_output else subprocess.DEVNULL
                dw_process = self._kubectl_run(
                    debug_cmd,
                    stdin=subprocess.PIPE,
                    stdout=redir,
                    stderr=redir,
                    text=True,
                )

                # sleep a bit to ensure debug container is up before sending the script
                time.sleep(3)

                # check debug container name
                result = self._kubectl_run(
                    ["get", "pod", "-n", self.namespace, self.pod, "-o", "json"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.pod_data.refresh(json.loads(result.stdout))
                new_e_containers = self.pod_data.get_ephemeral_container_names()
                debug_container_name = list(set(new_e_containers) - set(e_containers))[0]
                print(f"Debug container started: {debug_container_name}")
                copy_container = debug_container_name

            finally:
                # Clean up temp file
                if custom_file:
                    custom_file.close()

        # Copy the file from pod
        # Choose one of: kubectl_cp, kubectl_tar_cp, kubectl_chunked_cp
        kubectl_chunked_cp(self.namespace, self.pod, copy_container, dump_file, local_file)

        if dw_process is not None:
            # Signal debug container to stop
            print("Signaling debug container to stop...")
            self._kubectl_run(
                ["exec", "-n", self.namespace, self.pod, "--container", copy_container, "--", "touch", "/tmp/stopfile"],
                check=True,
            )

    def _check_container_tools(self, namespace:str, pod:str, container:str, tools:list):
        check_script = (
            "missing=''; "
            "for t in " + " ".join(tools) + "; do "
            "  p=$(command -v \"$t\" 2>/dev/null || true); "
            "  if [ -z \"$p\" ] || [ ! -x \"$p\" ]; then missing=\"$missing $t\"; fi; "
            "done; "
            "if [ -n \"$missing\" ]; then "
            "  echo \"Missing or not executable:$missing\"; exit 1; "
            "fi"
        )

        try:
            self._kubectl_run(
                ["exec", "-n", namespace, pod, "--container", container, "--", "sh", "-c", check_script],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            # msg = (e.stdout or "").strip() or (e.stderr or "").strip() or str(e)
            return False

    def _debug_custom_file(self, uid, gid):
        custom_spec = { 
            "securityContext": {
                "runAsUser": uid,
                "runAsGroup": gid
            }
        }

        # Write custom spec to temp file
        import tempfile
        custom_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json')
        json.dump(custom_spec, custom_file)
        custom_file.flush()
        return custom_file

class PodData:
    def __init__(self, pod_data):
        self.refresh(pod_data)

    def refresh(self, pod_data):
        self.pod_data = pod_data

    def get_default_container(self):
        # Extract default container name
        containers = self.pod_data.get("spec", {}).get("containers", [])
        if not containers:
            print(f"Error: No containers found in pod {self.pod_data.get('metadata', {}).get('name', '')}", file=sys.stderr)
            sys.exit(1)

        # Default container is the first one, or one with specific annotation
        default_container = containers[0].get("name")
        annotations = self.pod_data.get("metadata", {}).get("annotations", {})
        if "kubectl.kubernetes.io/default-container" in annotations:
            default_container = annotations["kubectl.kubernetes.io/default-container"]

        return default_container

    def get_container_uid_gid(self, container_name):
        container_statuses = self.pod_data.get("status", {}).get("containerStatuses", [])
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
            for container in self.pod_data.get("spec", {}).get("containers", []):
                if container.get("name") == container_name:
                    container_spec = container
                    break
            
            if container_spec:
                security_context = container_spec.get("securityContext", {})
                pod_security_context = self.pod_data.get("spec", {}).get("securityContext", {})
                
                if uid is None:
                    uid = security_context.get("runAsUser") or pod_security_context.get("runAsUser")
                if gid is None:
                    gid = security_context.get("runAsGroup") or pod_security_context.get("runAsGroup")

        return uid, gid
    
    def get_ephemeral_container_names(self):
        names = []
        ephemeral_containers = self.pod_data.get("spec", {}).get("ephemeralContainers", [])
        for ec in ephemeral_containers:
            names.append(ec.get("name"))
        return names
    
    def has_ephemeral_container_terminated(self, container_name):
        ephemeral_containers = self.pod_data.get("status", {}).get("ephemeralContainerStatuses", [])
        for ec in ephemeral_containers:
            name = ec.get("name")
            if name != container_name:
                continue

            # check if terminated
            state = ec.get("state", {})
            if "terminated" in state:
                return True
            
        return False
    