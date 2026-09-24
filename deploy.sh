#!/usr/bin/env bash
# deploy.sh — Auto-deploy KTMTicBot to EC2
# Usage: bash deploy.sh
# Requires: EC2_KEY_PATH and EC2_HOST set as env vars, OR edit defaults below.

set -euo pipefail

EC2_HOST="${EC2_HOST:-ubuntu@172.31.8.142}"
EC2_KEY="${EC2_KEY_PATH:-~/.ssh/ktm-ec2.pem}"
APP_DIR="~/ktm-sniper"

echo "====================================="
echo " KTMTicBot EC2 Auto-Deploy"
echo "====================================="
echo "Host   : $EC2_HOST"
echo "App dir: $APP_DIR"
echo ""

# Step 1: Push latest code to GitHub
echo "[1/3] Pushing latest code to GitHub..."
git push origin main
echo "      Done."

# Step 1.5: Sync persistent session cookies & SQLite database (if available) to EC2
if [ -f "data/auth_state.json" ]; then
  echo "      Syncing data/auth_state.json to EC2..."
  ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no "$EC2_HOST" "mkdir -p $APP_DIR/data"
  scp -i "$EC2_KEY" -o StrictHostKeyChecking=no data/auth_state.json "$EC2_HOST:$APP_DIR/data/auth_state.json"
  echo "      Session credentials synced!"
fi

if [ -f "data/ktm_sniper.db" ]; then
  echo "      Syncing data/ktm_sniper.db (SQLite tasks & passengers) to EC2..."
  ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no "$EC2_HOST" "mkdir -p $APP_DIR/data"
  scp -i "$EC2_KEY" -o StrictHostKeyChecking=no data/ktm_sniper.db "$EC2_HOST:$APP_DIR/data/ktm_sniper.db"
  echo "      SQLite database synced!"
fi

# Step 2: SSH into EC2, pull latest code, rebuild and restart containers
echo "[2/3] Deploying to EC2..."
ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no "$EC2_HOST" bash <<'REMOTE'
  set -euo pipefail
  cd ~/ktm-sniper
  echo "  -> git pull origin main"
  git pull origin main
  echo "  -> docker compose build"
  docker compose build
  echo "  -> docker compose up -d"
  docker compose up -d --remove-orphans
  echo "  -> waiting 5s for container startup..."
  sleep 5
  echo "  -> container status:"
  docker compose ps
REMOTE

# Step 3: Tail logs briefly to verify startup
echo ""
echo "[3/3] Tailing EC2 logs (15 lines)..."
ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no "$EC2_HOST" \
  "cd ~/ktm-sniper && docker compose logs --tail=15 ktm_sniper_bot" || true

echo ""
echo "====================================="
echo " Deployment complete!"
echo "====================================="
