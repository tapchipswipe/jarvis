#!/bin/bash
set -euo pipefail
cd /data/second-brain
/opt/homebrew/bin/python3 -m brain.consolidation "$1" >> /data/second-brain/logs/consolidation.log 2>&1
