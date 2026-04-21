#!/usr/bin/env bash
# deploy.sh — push meetingnotes to a remote server
#
# Customise HOST, REMOTE_DIR, and SERVICE for your deployment before use.
#
# Usage:
#   ./deploy/deploy.sh           # sync code + restart
#   ./deploy/deploy.sh setup     # first-time setup
#   ./deploy/deploy.sh restart   # restart service only

set -euo pipefail

HOST="${MEETINGNOTES_HOST:-yourserver}"
REMOTE_DIR="${MEETINGNOTES_REMOTE_DIR:-/opt/meetingnotes}"
SERVICE="${MEETINGNOTES_SERVICE:-meetingnotes}"
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
    scp "$LOCAL_SRC/deploy/meetingnotes.service" "$HOST:/tmp/meetingnotes.service"
    ssh "$HOST" "sudo cp /tmp/meetingnotes.service /etc/systemd/system/meetingnotes.service && sudo systemctl daemon-reload"

    log "Restarting $SERVICE ..."
    ssh "$HOST" "sudo systemctl restart $SERVICE"
    ssh "$HOST" "sudo systemctl status $SERVICE --no-pager -l"
    log "Done. Deploy complete on $HOST."
}

setup() {
    log "=== First-time setup on $HOST ==="

    # SERVICE_USER is the Linux user that will run the process.
    # Set MEETINGNOTES_USER or it defaults to the current SSH user.
    SERVICE_USER="${MEETINGNOTES_USER:-$(whoami)}"

    ssh "$HOST" "
        sudo mkdir -p $REMOTE_DIR
        sudo chown \$USER:\$USER $REMOTE_DIR
        sudo mkdir -p /var/lib/meetingnotes
        sudo chown $SERVICE_USER:$SERVICE_USER /var/lib/meetingnotes
        sudo mkdir -p /var/log/meetingnotes
        sudo chown $SERVICE_USER:$SERVICE_USER /var/log/meetingnotes
    "

    log "Creating venv ..."
    ssh "$HOST" "python3 -m venv $REMOTE_DIR/venv"

    deploy

    log "Installing systemd service ..."
    scp "$LOCAL_SRC/deploy/meetingnotes.service" "$HOST:/tmp/meetingnotes.service"
    ssh "$HOST" "
        sudo cp /tmp/meetingnotes.service /etc/systemd/system/meetingnotes.service
        sudo systemctl daemon-reload
        sudo systemctl enable meetingnotes
    "

    log ""
    log "Copy .env.example to $REMOTE_DIR/.env and fill in values, then:"
    log "  ssh $HOST sudo systemctl start meetingnotes"
}

case "${1:-deploy}" in
    setup)   setup ;;
    restart) ssh "$HOST" "sudo systemctl restart $SERVICE" ;;
    deploy)  deploy ;;
    *)       echo "Usage: $0 [deploy|setup|restart]"; exit 1 ;;
esac
