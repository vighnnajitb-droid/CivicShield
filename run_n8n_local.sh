#!/usr/bin/env bash
set -e

# Define data directory relative to project root
DATA_DIR="$(pwd)/data/n8n"

# Ensure n8n uses this specific directory to keep environments isolated
export N8N_USER_FOLDER="$DATA_DIR"
export N8N_PORT=5678

# Ensure CivicShield secrets match exactly what n8n expects
export INTERNAL_API_KEY="test_internal_key"

# Stop existing if any
kill $(lsof -t -i:5678) 2>/dev/null || true

# Wait for process to fully exit
sleep 2

# We must import workflows if the database is clean
if [ ! -f "$DATA_DIR/database.sqlite" ]; then
    echo "Importing workflows..."
    npm exec n8n import:workflow --input="$DATA_DIR/workflows.json"
fi

# The update:workflow command is deprecated in newer n8n versions.
# We will use our Python patch script instead after n8n initializes its DB.

echo "Starting n8n locally on port 5678..."
npm exec n8n start
