#!/bin/bash
set -euo pipefail
cd /Users/lucasdespot/second_brain
/opt/homebrew/bin/python3 -m brain.sync.pusher >> /Users/lucasdespot/second_brain/logs/pusher.log 2>&1
