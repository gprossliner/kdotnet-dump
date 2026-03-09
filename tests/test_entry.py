#!/usr/bin/env python3
"""
Integration tests for entry.py against test deployments
"""

import subprocess
import pytest
import yaml
from pathlib import Path


# Path to project root
PROJECT_ROOT = Path(__file__).parent.parent
ENTRY_PY = PROJECT_ROOT / "src" / "kdotnet_dump" / "cli.py"
TEST_MANIFEST = PROJECT_ROOT / "tests" / "manifest.yaml"
TEST_NAMESPACES = [
    "kdotnet-dump-test-baseline",
    "kdotnet-dump-test-restricted",
]


def load_test_deployments():
    """Load all deployments from and extract their labels"""
    with open(TEST_MANIFEST, 'r') as f:
        docs = list(yaml.safe_load_all(f))
    
    deployments = []
    for doc in docs:
        if doc and doc.get('kind') == 'Deployment':
            name = doc['metadata']['name']
            namespace = doc['metadata'].get('namespace', 'default')
            labels = doc['spec']['template']['metadata']['labels']
            # Get the first label for selector
            label_key = list(labels.keys())[0]
            label_value = labels[label_key]
            selector = f"{label_key}={label_value}"
            deployments.append({
                'name': name,
                'namespace': namespace,
                'selector': selector,
                'labels': labels
            })
    
    return deployments


def build_test_cases():
    """Build deployment/strategy test matrix."""
    test_cases = []
    for deployment in load_test_deployments():
        test_cases.append({"deployment": deployment, "strategy": "debug-container"})
        if deployment["name"].endswith("-root"):
            test_cases.append({"deployment": deployment, "strategy": "same-container"})
    return test_cases


@pytest.fixture(scope="session")
def ensure_deployments():
    """Ensure test deployments are created in the cluster"""
    subprocess.run(
        ["kubectl", "apply", "-f", str(TEST_MANIFEST)],
        check=True,
        capture_output=True
    )
    
    # Wait for pods in each test namespace to be ready
    for namespace in TEST_NAMESPACES:
        subprocess.run(
            [
                "kubectl",
                "wait",
                "--for=condition=ready",
                "pod",
                "--all",
                "-n",
                namespace,
                "--timeout=120s",
            ],
            check=True,
            capture_output=True,
        )
    
    yield
    
    # Cleanup is optional, keep pods for debugging
    # subprocess.run(["kubectl", "delete", "-f", str(TEST_MANIFEST)], check=False)


@pytest.mark.parametrize(
    "test_case",
    build_test_cases(),
    ids=lambda case: f"{case['deployment']['name']}[{case['strategy']}]",
)
def test_dump_creation(test_case, ensure_deployments):
    """Test dump creation for each deployment"""

    deployment = test_case["deployment"]
    strategy = test_case["strategy"]
    namespace = deployment['namespace']
    selector = deployment['selector']
    name = deployment['name']
    
    print(f"\n=== Testing {name} in namespace {namespace} with selector {selector} ===")
    
    # Clean up any existing dump file
    dump_file = PROJECT_ROOT / "latest_dump"
    if dump_file.exists():
        dump_file.unlink()
    
    # Run entry.py directly
    result = subprocess.run(
        [
            "python3", str(ENTRY_PY),
            "-n", namespace,
            "-l", selector,
            "--strategy", strategy,
            "--dump-type", "mini"  # Use mini for faster tests
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT
    )
    
    # Check for successful completion
    assert result.returncode == 0, f"entry.py failed for {name}:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    
    # Check that dump file was created
    assert dump_file.exists(), f"Dump file not created for {name}"
    
    # Check that dump file has reasonable size (at least 1KB)
    file_size = dump_file.stat().st_size
    assert file_size > 1024, f"Dump file too small for {name}: {file_size} bytes"
    
    print(f"✓ Successfully created dump for {name} ({file_size} bytes)")
    
    # Clean up dump file after successful test
    dump_file.unlink()


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
