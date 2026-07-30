#!/bin/bash
set -e
echo "Deploying to Lightspeed..."
scp jarvis/collectors/inbox.py despo@lightspeed:C:/data/jarvis/jarvis/collectors/inbox.py
scp jarvis/push_memories.py despo@lightspeed:C:/data/jarvis/jarvis/push_memories.py
scp jarvis/sync/push.py despo@lightspeed:C:/data/jarvis/jarvis/sync/push.py
echo "Deployed successfully"
