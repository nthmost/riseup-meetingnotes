#!/usr/bin/env bash
# deploy.sh — push meetingnotes to a remote Linux server (behind Apache2).
#
# The only value you MUST supply is the SSH host. Put it in a gitignored
# deploy/deploy.env (see deploy/deploy.env.example), or pass it inline:
#
#   MEETINGNOTES_HOST=myserver ./deploy/deploy.sh
#
# Everything else defaults to this project's real deployment layout and can
# be overridden with the MEETINGNOTES_* variables below.
#
# Usage:
#   ./deploy/deploy.sh           # sync code + pip install + restart service
#   ./deploy/deploy.sh setup     # first-time setup (dirs, venv, systemd units)
#   ./deploy/deploy.sh restart   # restart service only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_SRC="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load optional local overrides (gitignored) — at minimum the SSH host.
[ -f "$SCRIPT_DIR/deploy.env" ] && source "$SCRIPT_DIR/deploy.env"

HOST="${MEETINGNOTES_HOST:?Set MEETINGNOTES_HOST (e.g. in deploy/deploy.env) to the server SSH host}"
REMOTE_DIR="${MEETINGNOTES_REMOTE_DIR:-/opt/nbmeetingnotes}"
SERVICE="${MEETINGNOTES_SERVICE:-meetingnotes}"
SERVICE_USER="${MEETINGNOTES_USER:-nthmost}"
LOG_DIR="${MEETINGNOTES_LOG_DIR:-/var/log/nbarchive}"
DATA_DIR="${MEETINGNOTES_DATA_DIR:-/var/lib/nbmeetingnotes}"

log() { echo "▶ $*"; }

sync_code() {
    log "Syncing app to $HOST:$REMOTE_DIR ..."
    rsync -av --delete \
        --exclude '__pycache__' --exclude '*.pyc' \
        --exclude '.env' --exclude 'venv/' --exclude '.venv/' \
        --exclude '.git/' --exclude 'deploy/' --exclude 'data/' \
        --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
        "$LOCAL_SRC/" "$HOST:$REMOTE_DIR/"
}

sync_units() {
    log "Syncing systemd unit files ..."
    scp "$SCRIPT_DIR"/meetingnotes.service \
        "$SCRIPT_DIR"/meetingnotes-fetch.service \
        "$SCRIPT_DIR"/meetingnotes-fetch.timer \
        "$HOST:/tmp/"
    ssh "$HOST" "
        sudo cp /tmp/meetingnotes.service       /etc/systemd/system/meetingnotes.service
        sudo cp /tmp/meetingnotes-fetch.service /etc/systemd/system/meetingnotes-fetch.service
        sudo cp /tmp/meetingnotes-fetch.timer   /etc/systemd/system/meetingnotes-fetch.timer
        sudo systemctl daemon-reload
    "
}

deploy() {
    sync_code

    log "Installing deps ..."
    ssh "$HOST" "$REMOTE_DIR/venv/bin/pip install -q -r $REMOTE_DIR/requirements.txt"

    sync_units

    log "Restarting $SERVICE ..."
    ssh "$HOST" "sudo systemctl restart $SERVICE"
    ssh "$HOST" "systemctl status $SERVICE --no-pager -l | head -n 5"
    log "Done. Deploy complete on $HOST."
}

setup() {
    log "=== First-time setup on $HOST (user=$SERVICE_USER) ==="

    ssh "$HOST" "
        sudo mkdir -p $REMOTE_DIR   && sudo chown $SERVICE_USER:$SERVICE_USER $REMOTE_DIR
        sudo mkdir -p $DATA_DIR/raw && sudo chown -R $SERVICE_USER:$SERVICE_USER $DATA_DIR
        sudo mkdir -p $LOG_DIR      && sudo chown $SERVICE_USER:$SERVICE_USER $LOG_DIR
    "

    log "Creating venv ..."
    ssh "$HOST" "python3 -m venv $REMOTE_DIR/venv"

    sync_code
    ssh "$HOST" "$REMOTE_DIR/venv/bin/pip install -q -r $REMOTE_DIR/requirements.txt"
    sync_units

    log "Enabling units ..."
    ssh "$HOST" "sudo systemctl enable $SERVICE meetingnotes-fetch.timer"

    log ""
    log "Now copy your .env to the server and start the service:"
    log "  scp .env $HOST:$REMOTE_DIR/.env"
    log "  ssh $HOST sudo systemctl start $SERVICE"
}

case "${1:-deploy}" in
    setup)   setup ;;
    restart) ssh "$HOST" "sudo systemctl restart $SERVICE" ;;
    deploy)  deploy ;;
    *)       echo "Usage: $0 [deploy|setup|restart]"; exit 1 ;;
esac
