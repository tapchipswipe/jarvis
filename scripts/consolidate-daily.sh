#!/bin/bash
set -euo pipefail
exec /Users/lucasdespot/jarvis/scripts/run-if-idle.sh /bin/bash /data/jarvis/scripts/run-consolidation.sh daily
