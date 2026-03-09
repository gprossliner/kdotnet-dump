#!/usr/bin/env python3

import os
import sys
import argparse
from importlib.metadata import PackageNotFoundError, version

# Support both package import and direct execution (like used in tests)
try:
    from . import dumper
except ImportError:
    import dumper # type: ignore


def _resolve_version() -> str:
    try:
        package_version = version("kdotnet-dump")
        return f"v{package_version}"
    except PackageNotFoundError:
        return "unknown"

def main():
    parser = argparse.ArgumentParser(
        description="Create and download a .NET dump from a Kubernetes pod"
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {_resolve_version()}",
        help="Show kdotnet-dump version and exit",
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

    # Validate args
    if not args.pod and not args.selector:
        print(
            "Error: Either pod name or --selector (-l) must be specified", file=sys.stderr
        )
        parser.print_help()
        sys.exit(1)

    # check if env variable KDOTNET_DUMP_VERBOSE is set to a truthy value, and if so, set verbose_output to True
    verbose_env = os.getenv("KDOTNET_DUMP_VERBOSE", "").lower()
    verbose_output = verbose_env in ("1", "true", "yes", "on")

    dumper_instance = dumper.Dumper(
        namespace=args.namespace,
        pod=args.pod,
        selector=args.selector,
        dump_type=args.dump_type,
        dump_pid=args.dump_pid,
        strategy=args.strategy,
        debug_image=args.debug_image,
        verbose_output=verbose_output,
    )
    dumper_instance.run()
    

if __name__ == "__main__":
    main()
