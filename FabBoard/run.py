#!/usr/bin/env python3
"""
FabBoard Phase 3 - Sync Worker Launcher
Production startup script for FabBoard with background sync worker
"""

import os
import sys

# Production configuration
os.environ['FLASK_ENV'] = 'production'
os.environ['FLASK_DEBUG'] = '0'

# Ensure we're in the right directory
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

print('[Bootstrap] FabBoard Phase 3 - Sync Worker + Cache')
print('[Bootstrap] Loading app...')

from app import app
from sync_worker import start_sync_worker, get_sync_worker
from models import init_db

# Initialize database first
print('[Bootstrap] Initializing database...')
init_db()

# Start sync worker explicitly
print('[Bootstrap] Starting background sync worker...')
try:
    worker = start_sync_worker(poll_interval=10)
    if worker and worker.running:
        print(f'[Bootstrap] ✅ Sync worker running (poll_interval={worker.poll_interval}s)')
    else:
        print(f'[Bootstrap] ⚠️  Worker created but may not be running')
except Exception as e:
    print(f'[Bootstrap] ❌ Error starting worker: {e}')
    
# Start Flask app
port = int(os.environ.get('FABBOARD_PORT', 5580))
print(f'[Bootstrap] Starting Flask App on http://localhost:{port}')
print('-' * 60)

app.run(
    host='0.0.0.0',
    port=port,
    debug=False,
    use_reloader=False,
    threaded=True
)
