#!/usr/bin/env bash
# deploy.sh — push nbmeetingnotes to a remote server
#
# Customise HOST, REMOTE_DIR, and SERVICE for your deployment before use.
#
# Usage:
#   ./deploy/deploy.sh           # sync code + restart
#   ./deploy/deploy.sh setup     # first-time setup
#   ./deploy/deploy.sh restart   # restart service only

set -euo pipefail

HOST="${NBARCHIVE_HOST:-zephyr}"
REMOTE_DIR="${NBARCHIVE_REMOTE_DIR:-/opt/nbmeetingnotes}"
SERVICE="${NBARCHIVE_SERVICE:-nbmeetingnotes}"
LOCAL_SRC="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "▶ $*"; }

deploy() {
    log "Syncing app to $HOST:$REMOTE_DIR ..."
    rsync -av --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        --exclude '.env' --exclude 'venv/' --exclude '.venv/' \
        --exclude '.git/' --exclude 'deploy/' \
        --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
        "$LOCAL_SRC/" "$HOST:$REMOTE_DIR/"

    log "Installing deps ..."
    ssh "$HOST" "$REMOTE_DIR/venv/bin/pip install -q -r $REMOTE_DIR/requirements.txt"

    log "Syncing systemd service file ..."
    scp "$LOCAL_SRC/deploy/$SERVICE.service" "$HOST:/tmp/$SERVICE.service"
    ssh "$HOST" "sudo cp /tmp/$SERVICE.service /etc/systemd/system/$SERVICE.service && sudo systemctl daemon-reload"

    log "Restarting $SERVICE ..."
    ssh "$HOST" "sudo systemctl restart $SERVICE"
    ssh "$HOST" "sudo systemctl status $SERVICE --no-pager -l"
    log "Done. Deploy complete on $HOST."
}

setup() {
    log "=== First-time setup on $HOST ==="

    ssh "$HOST" "
        sudo mkdir -p $REMOTE_DIR
        sudo chown nthmost:nthmost $REMOTE_DIR
        sudo mkdir -p /var/www/nthmost.net/nbmeetingnotes
        sudo chown nthmost:nthmost /var/www/nthmost.net/nbmeetingnotes
        sudo mkdir -p /var/lib/nbmeetingnotes
        sudo chown nthmost:nthmost /var/lib/nbmeetingnotes
        sudo mkdir -p /var/log/nbmeetingnotes
        sudo chown nthmost:nthmost /var/log/nbmeetingnotes
    "

    log "Creating venv ..."
    ssh "$HOST" "python3 -m venv $REMOTE_DIR/venv"

    deploy

    log "Installing systemd service ..."
    scp "$LOCAL_SRC/deploy/nbmeetingnotes.service" "$HOST:/tmp/nbmeetingnotes.service"
    ssh "$HOST" "
        sudo cp /tmp/nbmeetingnotes.service /etc/systemd/system/nbmeetingnotes.service
        sudo systemctl daemon-reload
        sudo systemctl enable nbmeetingnotes
    "

    log ""
    log "Copy .env.example to $REMOTE_DIR/.env and fill in values, then:"
    log "  ssh $HOST sudo systemctl start nbmeetingnotes"
}

case "${1:-deploy}" in
    setup)   setup ;;
    restart) ssh "$HOST" "sudo systemctl restart $SERVICE" ;;
    deploy)  deploy ;;
    *)       echo "Usage: $0 [deploy|setup|restart]"; exit 1 ;;
esac
