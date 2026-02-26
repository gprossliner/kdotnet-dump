#!/bin/sh

set -eu

# test installation and tool registration
pip install -e .
kdotnet-dump --help
