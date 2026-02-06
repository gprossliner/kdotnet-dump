#!/usr/bin/env python3
"""
Integration tests for entry.py against test deployments
"""

import subprocess
import os
import pytest
import yaml
from pathlib import Path


# Path to project root
PROJECT_ROOT = Path(__file__).parent.parent
ENTRY_PY = PROJECT_ROOT / "entry.py"
TEST_MANIFEST = PROJECT_ROOT / "tests" / "manifest.yaml"


def load_test_deployments():
    """Load all deployments from and extract their labels"""
    with open(TEST_MANIFEST, 'r') as f:
        docs = list(yaml.safe_load_all(f))
    
    deployments = []
    for doc in docs:
        if doc and doc.get('kind') == 'Deployment':
            name = doc['metadata']['name']
            labels = doc['spec']['template']['metadata']['labels']
            # Get the first label for selector
            label_key = list(labels.keys())[0]
            label_value = labels[label_key]
            selector = f"{label_key}={label_value}"
            deployments.append({
                'name': name,
                'selector': selector,
                'labels': labels
            })
    
    return deployments


@pytest.fixture(scope="session")
def ensure_deployments():
    """Ensure test deployments are created in the cluster"""
    subprocess.run(
        ["kubectl", "apply", "-f", str(TEST_MANIFEST)],
        check=True,
        capture_output=True
    )
    
    # Wait for pods to be ready
    subprocess.run(
        ["kubectl", "wait", "--for=condition=ready", "pod", "--all", "--timeout=120s"],
        check=True,
        capture_output=True
    )
    
    yield
    
    # Cleanup is optional, keep pods for debugging
    # subprocess.run(["kubectl", "delete", "-f", str(TEST_MANIFEST)], check=False)


@pytest.mark.parametrize("deployment", load_test_deployments(), ids=lambda d: d['name'])
def test_dump_creation(deployment, ensure_deployments):
    """Test dump creation for each deployment"""
    
    selector = deployment['selector']
    name = deployment['name']
    
    print(f"\n=== Testing {name} with selector {selector} ===")
    
    # Clean up any existing dump file
    dump_file = PROJECT_ROOT / "latest_dump"
    if dump_file.exists():
        dump_file.unlink()
    
    # Run entry.py
    result = subprocess.run(
        [
            "python3", str(ENTRY_PY),
            "-l", selector,
            "--strategy", "debug-container",
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
