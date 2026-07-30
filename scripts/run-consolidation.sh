#!/bin/bash
set -euo pipefail
cd /data/jarvis
/opt/homebrew/bin/python3 -m jarvis.consolidation "$1" >> /data/jarvis/logs/consolidation.log 2>&1
