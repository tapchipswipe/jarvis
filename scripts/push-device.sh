#!/bin/bash
set -euo pipefail
cd /Users/lucasdespot/jarvis
/opt/homebrew/bin/python3 -m jarvis.sync.pusher >> /Users/lucasdespot/jarvis/logs/pusher.log 2>&1
