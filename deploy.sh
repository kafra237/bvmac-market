#!/usr/bin/env bash
set -Eeuo pipefail

# BVMAC Market deployment entry point.
# Run it locally (Linux/macOS/Git Bash/WSL) and it will copy the repository to
# the VPS, or run it on the VPS with --server.
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage(){ cat <<'EOF'
BVMAC Market deployment

Local/remote mode (recommended):
  ./deploy.sh [--host HOST] [--user USER] [--domain DOMAIN] [--email EMAIL]
              [--ssh-port PORT] [--pipeline auto|replace]

Server mode (normally called automatically):
  sudo BVMAC_DOMAIN=market.example.org BVMAC_LE_EMAIL=admin@example.org \
       bash deploy.sh --server

The script never stores SSH passwords. ssh/scp ask for them when needed.
EOF
}

if [ "${1:-}" != "--server" ]; then
  HOST=""; SSH_USER=""; DOMAIN=""; EMAIL=""; SSH_PORT="22"; PIPELINE_POLICY="auto"
  while [ $# -gt 0 ]; do
    case "$1" in
      --host) HOST="${2:-}"; shift 2;;
      --user) SSH_USER="${2:-}"; shift 2;;
      --domain) DOMAIN="${2:-}"; shift 2;;
      --email) EMAIL="${2:-}"; shift 2;;
      --ssh-port) SSH_PORT="${2:-}"; shift 2;;
      --pipeline) PIPELINE_POLICY="${2:-}"; shift 2;;
      -h|--help) usage; exit 0;;
      *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
  done
  [ -n "$HOST" ] || read -r -p "VPS host or IP: " HOST
  [ -n "$SSH_USER" ] || { read -r -p "SSH user [ubuntu]: " SSH_USER; SSH_USER="${SSH_USER:-ubuntu}"; }
  [ -n "$DOMAIN" ] || read -r -p "Public domain (for example market.example.org): " DOMAIN
  [ -n "$EMAIL" ] || read -r -p "Administrator / Let's Encrypt email: " EMAIL
  [[ "$HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || { echo "Invalid VPS host." >&2; exit 2; }
  [[ "$SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { echo "Invalid SSH user." >&2; exit 2; }
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid domain." >&2; exit 2; }
  [[ "$EMAIL" == *@*.* ]] || { echo "Invalid email." >&2; exit 2; }
  [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || { echo "Invalid SSH port." >&2; exit 2; }
  case "$PIPELINE_POLICY" in auto|replace) ;; *) echo "--pipeline must be auto or replace" >&2; exit 2;; esac

  for cmd in tar ssh scp; do command -v "$cmd" >/dev/null || { echo "Missing local command: $cmd" >&2; exit 1; }; done
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  ARCHIVE="$TMP/bvmac-market.tar.gz"
  echo "[INFO] Packaging repository..."
  tar -C "$BASE" -czf "$ARCHIVE" \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='*.pyc' --exclude='*.zip' .
  REMOTE_ARCHIVE="/home/$SSH_USER/bvmac-market.tar.gz"
  REMOTE_DIR="/home/$SSH_USER/bvmac-market-deploy"
  echo "[INFO] Uploading to $SSH_USER@$HOST:$SSH_PORT ..."
  scp -P "$SSH_PORT" "$ARCHIVE" "$SSH_USER@$HOST:$REMOTE_ARCHIVE"
  q_domain=$(printf '%q' "$DOMAIN"); q_email=$(printf '%q' "$EMAIL"); q_policy=$(printf '%q' "$PIPELINE_POLICY")
  ssh -tt -p "$SSH_PORT" "$SSH_USER@$HOST" \
    "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR' && tar -xzf '$REMOTE_ARCHIVE' -C '$REMOTE_DIR' && rm -f '$REMOTE_ARCHIVE' && sudo env BVMAC_DOMAIN=$q_domain BVMAC_LE_EMAIL=$q_email BVMAC_PIPELINE_POLICY=$q_policy bash '$REMOTE_DIR/deploy.sh' --server"
  echo
  echo "Deployment command completed. Run ./checking.sh with the same host/domain for an independent check."
  exit 0
fi
shift

set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "[FAIL] Run server-side deployment with sudo/root."
  exit 1
fi

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v flock >/dev/null 2>&1 || { echo "[FAIL] Missing required command: flock" >&2; exit 1; }
exec 9>/run/lock/bvmac-deploy.lock
flock -n 9 || { echo "[FAIL] Another BVMAC Market deployment is already running." >&2; exit 1; }
DOMAIN="${BVMAC_DOMAIN:?BVMAC_DOMAIN is required}"
LE_EMAIL="${BVMAC_LE_EMAIL:?BVMAC_LE_EMAIL is required}"
MASTER="${BVMAC_MASTER_FILE:-/var/lib/bvmac/output/bvmac_master.xlsx}"

APP_BASE="/opt/bvmac"
WEB_BASE="/var/www/bvmac"
ETC_DIR="/etc/bvmac"
DATA_DIR="/var/lib/bvmac"
DB="bvmac"
API_ROLE="bvmacapi"
IMPORT_ROLE="bvmacimport"
STATS_ROLE="bvmacstats"
DATA_GROUP="bvmacdata"
SECRET_GROUP="bvmacsecret"
API_PORT="8765"
CANDIDATE_PORT="18765"
SERVICE_PREFIX="bvmac"
SITE="/etc/nginx/sites-available/bvmac"
SITE_LINK="/etc/nginx/sites-enabled/bvmac"
SECRETS="$ETC_DIR/app.env"
STAT_ENV="$ETC_DIR/stat.env"
RAW_LOG="/var/log/nginx/bvmac_webstats_raw.log"
SPOOL_DIR="$DATA_DIR/webstats-spool"
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
CERT_FULLCHAIN="$CERT_DIR/fullchain.pem"
CERT_KEY="$CERT_DIR/privkey.pem"
MASTER_REPORT="$(dirname "$MASTER")/bvmac_download_report.json"
RELEASE_ID="${BVMAC_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
BACKUP="/var/backups/bvmac/$STAMP"
RELEASE="$APP_BASE/releases/$RELEASE_ID"
WEB_RELEASE="${WEB_BASE}-releases/$RELEASE_ID"
APP_CURRENT="$APP_BASE/current"
WEB_CURRENT="${WEB_BASE}-current"
CANDIDATE_UNIT="bvmac-candidate.service"
API_UNIT="bvmac-api.service"
IMPORT_UNIT="bvmac-db-import.service"
IMPORT_PATH="bvmac-db-import.path"
WEBSTATS_UNIT="bvmac-webstats.service"
WEBSTATS_TIMER="bvmac-webstats.timer"
ALERTS_UNIT="bvmac-alerts.service"
ALERTS_TIMER="bvmac-alerts.timer"
FEEDS_UNIT="bvmac-feeds.service"
FEEDS_TIMER="bvmac-feeds.timer"
NOTIF_UNIT="bvmac-notifications.service"
NOTIF_TIMER="bvmac-notifications.timer"
WEEKLY_UNIT="bvmac-weekly-digest.service"
WEEKLY_TIMER="bvmac-weekly-digest.timer"
WEEKLY_EMAIL_UNIT="bvmac-weekly-email.service"
WEEKLY_EMAIL_TIMER="bvmac-weekly-email.timer"
NEW_DATA_UNIT="bvmac-new-data-push.service"
NEW_DATA_PATH="bvmac-new-data-push.path"
PIPELINE_WATCH_UNIT="bvmac-pipeline-watch.service"
PIPELINE_WATCH_TIMER="bvmac-pipeline-watch.timer"
INITIAL_STAT_PASSWORD=""
CUTOVER_STARTED=0
NGINX_CHANGED=0
DB_MUTATION_STARTED=0
DB_EXISTED=0
DB_RESTORE_VERIFIED=0
OLD_APP_CURRENT=""
OLD_WEB_CURRENT=""

log(){ printf '\n[%s] %s\n' "$1" "$2"; }
die(){ echo "[ERREUR] $*" >&2; exit 1; }


install_operations(){
  local app_dir="$1" etc_dir="$2" db_name="$3" port="$4" prefix="$5"
  [[ "$prefix" =~ ^[a-zA-Z0-9-]+$ ]] || die "Invalid service prefix"
  install -d -m 0700 "/var/backups/${prefix}-daily"
  cat > "/etc/systemd/system/${prefix}-backup.service" <<UNIT
[Unit]
Description=BVMAC backup and restore verification
After=postgresql.service
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 $app_dir/scripts/maintenance.py --database $db_name --destination /var/backups/${prefix}-daily --config $etc_dir --app $app_dir
UNIT
  cat > "/etc/systemd/system/${prefix}-backup.timer" <<UNIT
[Unit]
Description=BVMAC daily backup
[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  cat > "/etc/systemd/system/${prefix}-monitor.service" <<UNIT
[Unit]
Description=BVMAC API and backup monitor
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $app_dir/scripts/monitor.py --port $port --backups /var/backups/${prefix}-daily --output $etc_dir/operations.json
UNIT
  cat > "/etc/systemd/system/${prefix}-monitor.timer" <<UNIT
[Unit]
Description=BVMAC monitor every five minutes
[Timer]
OnCalendar=*-*-* *:00/5:00
AccuracySec=30s
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  python3 "$app_dir/scripts/maintenance.py" --database "$db_name" --destination "/var/backups/${prefix}-daily" --config "$etc_dir" --app "$app_dir" --verify-restore
  systemctl daemon-reload
  systemctl enable --now "${prefix}-backup.timer" "${prefix}-monitor.timer"
  systemctl start "${prefix}-monitor.service"
}

required=(
  backend/api/launch_support.py backend/api/email_auth.py backend/api/admin_security.py
  web/i18n.js web/version.json web/launch.js web/launch.css web/information.html web/information.js web/demo.html web/demo.js web/share.png web/auth-ui.js web/auth-page.js web/auth.html web/admin-auth.js web/email-settings.js
  operations/admin_code.py operations/maintenance.py operations/monitor.py operations/patch_nginx_app_routes.py
  backend/api/bvmac_api.py backend/api/common.py backend/api/auth_module.py backend/api/market.py
  backend/api/portfolio_module.py backend/api/portfolio_valuation.py backend/api/feedback_module.py backend/api/user_tools.py backend/api/push_module.py backend/api/push_service.py backend/api/feed_module.py backend/api/admin_module.py backend/api/admin_communications.py backend/api/weekly_email.py backend/api/ml_radar.py
  backend/api/browser_analytics.py backend/admin/stat.html
  database/schema.sql database/application.sql database/portfolio_workspace.sql database/notification_inbox.sql database/weekly_email.sql database/admin_communications.sql database/ml_radar.sql database/import_excel.py
  web/index.html web/app-analysis.html web/app-portfolios.html web/app-feeds.html web/feeds-app.js web/auth.html web/index-app.js web/lab-app.js web/auth-page.js
  web/account-app.js web/stat-app.js web/analytics.js web/radar-app.js web/radar.css web/shell.css web/shell.js web/workspace.css web/chart-tools.js
  web/chart.umd.min.js web/chartjs-plugin-zoom.min.js web/landing-fixes.css web/portfolio-workspace.css web/portfolio-analysis.js web/lab-workspace.js web/instrument-analysis.js web/instrument-analysis.css web/notification-inbox.js web/experience.css
  backend/jobs/webstats.py backend/jobs/alerts.py backend/jobs/feeds.py backend/jobs/notifications.py operations/set_stat_password.py operations/generate_vapid.py operations/new_data_push.py operations/pipeline_watch.py ml/infer.py ml/models/trade_1.joblib ml/models/trade_5.joblib ml/models/up_20.joblib ml/models/opcvm_down_next.joblib ml/validation/action_ticker_reliability.json ml/validation/opcvm_fund_reliability.json
  pipeline/bvmac_downloader.py pipeline/bvmac_extract.py pipeline/run.py requirements.txt
)
for rel in "${required[@]}"; do
  [ -f "$BASE/$rel" ] || die "Missing repository file : $rel"
done

pipeline_manifest(){
  local p
  {
    for p in "$APP_BASE/pipeline/run.py" "$APP_BASE/pipeline/bvmac_downloader.py" "$APP_BASE/pipeline/bvmac_extract.py" \
             /etc/systemd/system/bvmac-pipeline.service /etc/systemd/system/bvmac-pipeline.timer; do
      if [ -f "$p" ]; then sha256sum "$p"; else printf 'MISSING  %s\n' "$p"; fi
    done
  } | sort
}

restore_file_or_remove(){
  local saved="$1" target="$2"
  if [ -e "$saved" ]; then
    install -D -m 0644 "$saved" "$target"
  else
    rm -f "$target"
  fi
}

rollback(){
  local rc=$?
  trap - ERR
  echo
  echo "[ROLLBACK] Deployment failed (code $rc)."
  systemctl disable --now "$CANDIDATE_UNIT" 2>/dev/null || true
  rm -f "/etc/systemd/system/$CANDIDATE_UNIT"

  if [ "$DB_MUTATION_STARTED" = 1 ]; then
    echo "[ROLLBACK] Restoring the previous PostgreSQL state."
    systemctl stop "$API_UNIT" "$IMPORT_PATH" "$IMPORT_UNIT" 2>/dev/null || true
    if [ "$DB_EXISTED" = 1 ] && [ -s "$BACKUP/database/bvmac-postgresql.dump" ]; then
      runuser -u postgres -- pg_restore --clean --if-exists --exit-on-error -d "$DB" "$BACKUP/database/bvmac-postgresql.dump" >/dev/null 2>&1 || echo "[ROLLBACK] WARNING: PostgreSQL restore may be incomplete" >&2
    elif [ "$DB_EXISTED" = 0 ]; then
      runuser -u postgres -- dropdb --if-exists "$DB" >/dev/null 2>&1 || true
    fi
  fi

  if [ "$CUTOVER_STARTED" = 1 ] || [ "$NGINX_CHANGED" = 1 ]; then
    echo "[ROLLBACK] Restoring the previous API/Nginx state."

    for u in "$API_UNIT" "$IMPORT_UNIT" "$IMPORT_PATH" "$WEBSTATS_UNIT" "$WEBSTATS_TIMER" "$ALERTS_UNIT" "$ALERTS_TIMER" "$FEEDS_UNIT" "$FEEDS_TIMER" "$NOTIF_UNIT" "$NOTIF_TIMER" "$WEEKLY_UNIT" "$WEEKLY_TIMER" "$WEEKLY_EMAIL_UNIT" "$WEEKLY_EMAIL_TIMER" "$NEW_DATA_UNIT" "$NEW_DATA_PATH" "$PIPELINE_WATCH_UNIT" "$PIPELINE_WATCH_TIMER" bvmac-pipeline.service bvmac-pipeline.timer; do
      systemctl disable --now "$u" 2>/dev/null || true
      rm -f "/etc/systemd/system/$u"
      [ -f "$BACKUP/systemd/$u" ] && cp -a "$BACKUP/systemd/$u" "/etc/systemd/system/$u"
    done

    if [ -n "$OLD_APP_CURRENT" ]; then ln -sfn "$OLD_APP_CURRENT" "$APP_CURRENT"; else rm -f "$APP_CURRENT"; fi
    if [ -n "$OLD_WEB_CURRENT" ]; then ln -sfn "$OLD_WEB_CURRENT" "$WEB_CURRENT"; else rm -f "$WEB_CURRENT"; fi

    if [ -f "$BACKUP/nginx/bvmac" ]; then
      cp -a "$BACKUP/nginx/bvmac" "$SITE"
      ln -sfn "$SITE" "$SITE_LINK"
    elif [ -f "$BACKUP/nginx/site.absent" ]; then
      rm -f "$SITE" "$SITE_LINK"
    fi
    if [ -f "$BACKUP/nginx/bvmac-limits.conf" ]; then
      cp -a "$BACKUP/nginx/bvmac-limits.conf" /etc/nginx/conf.d/bvmac-limits.conf
    else
      rm -f /etc/nginx/conf.d/bvmac-limits.conf
    fi
    if [ -f "$BACKUP/nginx/bvmac-app.inc" ]; then
      cp -a "$BACKUP/nginx/bvmac-app.inc" /etc/nginx/snippets/bvmac-app.inc
    else
      rm -f /etc/nginx/snippets/bvmac-app.inc
    fi
    if [ -f "$BACKUP/nginx/bvmac-logformat.conf" ]; then
      cp -a "$BACKUP/nginx/bvmac-logformat.conf" /etc/nginx/conf.d/bvmac-logformat.conf
    else
      rm -f /etc/nginx/conf.d/bvmac-logformat.conf
    fi
    if [ -f "$BACKUP/nginx/bvmac-webstats.conf" ]; then
      cp -a "$BACKUP/nginx/bvmac-webstats.conf" /etc/nginx/conf.d/bvmac-webstats.conf
    else
      rm -f /etc/nginx/conf.d/bvmac-webstats.conf
    fi

    systemctl daemon-reload || true
    for u in "$API_UNIT" "$IMPORT_UNIT" "$IMPORT_PATH" "$WEBSTATS_UNIT" "$WEBSTATS_TIMER" "$ALERTS_UNIT" "$ALERTS_TIMER" "$FEEDS_UNIT" "$FEEDS_TIMER" "$NOTIF_UNIT" "$NOTIF_TIMER" "$WEEKLY_UNIT" "$WEEKLY_TIMER" "$WEEKLY_EMAIL_UNIT" "$WEEKLY_EMAIL_TIMER" "$NEW_DATA_UNIT" "$NEW_DATA_PATH" "$PIPELINE_WATCH_UNIT" "$PIPELINE_WATCH_TIMER" bvmac-pipeline.service bvmac-pipeline.timer; do
      if [ -f "$BACKUP/systemd/$u.enabled" ]; then
        state="$(head -n1 "$BACKUP/systemd/$u.enabled" 2>/dev/null || true)"
        [ "$state" = enabled ] && systemctl enable "$u" >/dev/null 2>&1 || systemctl disable "$u" >/dev/null 2>&1 || true
      fi
      if [ -f "$BACKUP/systemd/$u.active" ]; then
        state="$(head -n1 "$BACKUP/systemd/$u.active" 2>/dev/null || true)"
        [ "$state" = active ] && systemctl start "$u" >/dev/null 2>&1 || systemctl stop "$u" >/dev/null 2>&1 || true
      fi
    done
    nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
  fi

  echo "[ROLLBACK] Pipeline state restored from the deployment backup when applicable."
  echo "[ROLLBACK] Backup: $BACKUP"
  exit "$rc"
}
trap rollback ERR

log "1/14" "Read-only prechecks and backup of the current installation"
mkdir -p "$BACKUP"/{nginx,systemd,etc,legacy-app,legacy-web,database}
pipeline_manifest > "$BACKUP/pipeline.before.sha256"
systemctl is-enabled bvmac-pipeline.timer > "$BACKUP/pipeline.timer.enabled" 2>&1 || true
systemctl is-active bvmac-pipeline.timer > "$BACKUP/pipeline.timer.active" 2>&1 || true

[ -d "$APP_BASE/app" ] && cp -a "$APP_BASE/app/." "$BACKUP/legacy-app/" 2>/dev/null || true
[ -d "$WEB_BASE" ] && cp -a "$WEB_BASE/." "$BACKUP/legacy-web/" 2>/dev/null || true
[ -d "$ETC_DIR" ] && cp -a "$ETC_DIR/." "$BACKUP/etc/" 2>/dev/null || true
if [ -f "$SITE" ]; then cp -a "$SITE" "$BACKUP/nginx/bvmac"; else touch "$BACKUP/nginx/site.absent"; fi
[ -f /etc/nginx/conf.d/bvmac-limits.conf ] && cp -a /etc/nginx/conf.d/bvmac-limits.conf "$BACKUP/nginx/" || true
[ -f /etc/nginx/snippets/bvmac-app.inc ] && cp -a /etc/nginx/snippets/bvmac-app.inc "$BACKUP/nginx/" || true
[ -f /etc/nginx/conf.d/bvmac-logformat.conf ] && cp -a /etc/nginx/conf.d/bvmac-logformat.conf "$BACKUP/nginx/" || true
[ -f /etc/nginx/conf.d/bvmac-webstats.conf ] && cp -a /etc/nginx/conf.d/bvmac-webstats.conf "$BACKUP/nginx/" || true
for u in "$API_UNIT" "$IMPORT_UNIT" "$IMPORT_PATH" "$WEBSTATS_UNIT" "$WEBSTATS_TIMER" "$ALERTS_UNIT" "$ALERTS_TIMER" "$FEEDS_UNIT" "$FEEDS_TIMER" "$NOTIF_UNIT" "$NOTIF_TIMER" "$WEEKLY_UNIT" "$WEEKLY_TIMER" "$WEEKLY_EMAIL_UNIT" "$WEEKLY_EMAIL_TIMER" "$NEW_DATA_UNIT" "$NEW_DATA_PATH" "$PIPELINE_WATCH_UNIT" "$PIPELINE_WATCH_TIMER" bvmac-pipeline.service bvmac-pipeline.timer; do
  [ -f "/etc/systemd/system/$u" ] && cp -a "/etc/systemd/system/$u" "$BACKUP/systemd/" || true
  systemctl is-enabled "$u" > "$BACKUP/systemd/$u.enabled" 2>&1 || true
  systemctl is-active "$u" > "$BACKUP/systemd/$u.active" 2>&1 || true
done
[ -s "$MASTER" ] && cp -a "$MASTER" "$BACKUP/bvmac_master.xlsx" || true
OLD_APP_CURRENT="$(readlink -f "$APP_CURRENT" 2>/dev/null || true)"
OLD_WEB_CURRENT="$(readlink -f "$WEB_CURRENT" 2>/dev/null || true)"

log "2/14" "System dependencies"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-full python3-venv nginx curl postgresql acl ca-certificates
systemctl enable --now postgresql nginx

if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='$DB'" postgres 2>/dev/null | grep -qx 1; then
  DB_EXISTED=1
  log "2/14" "Backup PostgreSQL transactionnel et test de restauration"
  runuser -u postgres -- pg_dump -Fc "$DB" > "$BACKUP/database/bvmac-postgresql.dump"
  [ -s "$BACKUP/database/bvmac-postgresql.dump" ] || die "PostgreSQL backup is empty"
  runuser -u postgres -- pg_restore --list "$BACKUP/database/bvmac-postgresql.dump" >/dev/null
  RESTORE_DB="bvmac_restorecheck_${STAMP//[-_:]/}"
  RESTORE_DB="${RESTORE_DB:0:60}"
  runuser -u postgres -- createdb -T template0 "$RESTORE_DB"
  if runuser -u postgres -- pg_restore --exit-on-error -d "$RESTORE_DB" "$BACKUP/database/bvmac-postgresql.dump" >/dev/null 2>&1 \
     && runuser -u postgres -- psql -d "$RESTORE_DB" -qAt -c 'SELECT count(*) FROM pg_catalog.pg_class' | grep -Eq '^[1-9][0-9]*$'; then
    DB_RESTORE_VERIFIED=1
  else
    runuser -u postgres -- dropdb --if-exists "$RESTORE_DB" >/dev/null 2>&1 || true
    die "PostgreSQL backup failed the isolated restore test"
  fi
  runuser -u postgres -- dropdb --if-exists "$RESTORE_DB"
fi
cat > "$BACKUP/manifest.txt" <<EOF_MANIFEST
project=bvmac
source_release=${OLD_APP_CURRENT:-none}
release_id=$RELEASE_ID
deployment_mode=FULL
created_at=$STAMP
hostname=$(hostname)
app_path=$OLD_APP_CURRENT
data_path=$DATA_DIR
service_names=$API_UNIT,$IMPORT_UNIT,$IMPORT_PATH,$WEBSTATS_UNIT,$WEBSTATS_TIMER,$ALERTS_UNIT,$ALERTS_TIMER,$FEEDS_UNIT,$FEEDS_TIMER,$NOTIF_UNIT,$NOTIF_TIMER
nginx_files=$SITE,/etc/nginx/snippets/bvmac-app.inc
database_type=postgresql
database_backup=$( [ "$DB_EXISTED" -eq 1 ] && echo "$BACKUP/database/bvmac-postgresql.dump" || echo N/A )
restore_verified=$( [ "$DB_EXISTED" -eq 1 ] && [ "$DB_RESTORE_VERIFIED" -eq 1 ] && echo true || echo N/A )
checksums=$BACKUP/pipeline.before.sha256
EOF_MANIFEST
chmod 0600 "$BACKUP/manifest.txt" "$BACKUP/database/bvmac-postgresql.dump" 2>/dev/null || true

log "3/14" "System users, groups and directories"
getent group "$DATA_GROUP" >/dev/null || groupadd --system "$DATA_GROUP"
getent group "$SECRET_GROUP" >/dev/null || groupadd --system "$SECRET_GROUP"
for u in "$API_ROLE" "$IMPORT_ROLE" "$STATS_ROLE"; do
  id "$u" >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin "$u"
done
usermod -aG "$DATA_GROUP,$SECRET_GROUP" "$API_ROLE"
usermod -aG "$DATA_GROUP" "$IMPORT_ROLE"
usermod -aG "$SECRET_GROUP" "$STATS_ROLE"
install -d -o root -g root -m 0755 "$APP_BASE" "$APP_BASE/releases" "${WEB_BASE}-releases"
install -d -o root -g "$SECRET_GROUP" -m 0750 "$ETC_DIR"
install -d -o "$STATS_ROLE" -g "$STATS_ROLE" -m 0700 "$SPOOL_DIR"

log "4/14" "Prepare an isolated release"
install -d -o root -g root -m 0755 "$RELEASE" "$RELEASE/app" "$RELEASE/db" "$RELEASE/scripts" "$RELEASE/ml" "$RELEASE/ml/models" "$RELEASE/ml/validation" "$WEB_RELEASE"
for f in launch_support.py email_auth.py admin_security.py bvmac_api.py common.py auth_module.py market.py portfolio_module.py portfolio_valuation.py feedback_module.py user_tools.py push_module.py push_service.py feed_module.py admin_module.py admin_communications.py weekly_email.py ml_radar.py browser_analytics.py; do
  install -o root -g root -m 0644 "$BASE/backend/api/$f" "$RELEASE/app/$f"
done
install -o root -g root -m 0644 "$BASE/backend/admin/stat.html" "$RELEASE/app/stat.html"
for f in schema.sql application.sql portfolio_workspace.sql notification_inbox.sql weekly_email.sql admin_communications.sql ml_radar.sql; do
  install -o root -g root -m 0644 "$BASE/database/$f" "$RELEASE/db/$f"
done
install -o root -g root -m 0755 "$BASE/database/import_excel.py" "$RELEASE/db/import_excel.py"
install -o root -g root -m 0755 "$BASE/ml/infer.py" "$RELEASE/ml/infer.py"
install -o root -g root -m 0644 "$BASE/ml/model-manifest.json" "$RELEASE/ml/model-manifest.json"
install -o root -g root -m 0644 "$BASE/ml/VALIDATED_TARGETS.csv" "$RELEASE/ml/VALIDATED_TARGETS.csv"
install -o root -g root -m 0644 "$BASE/ml/MODEL_CARD.md" "$RELEASE/ml/MODEL_CARD.md"
for f in trade_1.joblib trade_5.joblib up_20.joblib opcvm_down_next.joblib; do install -o root -g root -m 0644 "$BASE/ml/models/$f" "$RELEASE/ml/models/$f"; done
for f in action_ticker_reliability.json opcvm_fund_reliability.json; do install -o root -g root -m 0644 "$BASE/ml/validation/$f" "$RELEASE/ml/validation/$f"; done
install -o root -g root -m 0755 "$BASE/backend/jobs/webstats.py" "$RELEASE/webstats_import_nginx.py"
install -o root -g root -m 0755 "$BASE/backend/jobs/alerts.py" "$RELEASE/alerts_evaluate.py"
install -o root -g root -m 0755 "$BASE/backend/jobs/feeds.py" "$RELEASE/feed_collector.py"
install -o root -g root -m 0755 "$BASE/backend/jobs/notifications.py" "$RELEASE/notifications_dispatch.py"
install -o root -g root -m 0755 "$BASE/operations/set_stat_password.py" "$RELEASE/scripts/set_stat_password.py"
install -o root -g root -m 0755 "$BASE/operations/generate_vapid.py" "$RELEASE/scripts/generate_vapid.py"
for f in admin_code.py maintenance.py monitor.py new_data_push.py pipeline_watch.py; do
  install -o root -g root -m 0755 "$BASE/operations/$f" "$RELEASE/scripts/$f"
done
install -o root -g root -m 0644 "$BASE/requirements.txt" "$RELEASE/requirements.txt"
for f in launch.js launch.css information.html information.js demo.html demo.js share.png manifest.webmanifest pwa.js sw.js version.json i18n.js pwa-192.png pwa-512.png apple-touch-icon.png favicon-32.png auth-ui.js auth-page.js auth.html admin-auth.js email-settings.js index.html app.html landing.css landing.js app-analysis.html app-portfolios.html app-feeds.html feeds-app.js index-app.js lab-app.js account-app.js stat-app.js analytics.js radar-app.js radar.css shell.css shell.js workspace.css chart-tools.js chart.umd.min.js chartjs-plugin-zoom.min.js landing-fixes.css portfolio-workspace.css portfolio-analysis.js lab-workspace.js instrument-analysis.js instrument-analysis.css notification-inbox.js experience.css; do
  install -o www-data -g www-data -m 0644 "$BASE/web/$f" "$WEB_RELEASE/$f"
done
# Every deployment gets a unique asset token. Browsers and installed PWAs can
# therefore discover a new release without relying on users to clear caches.
python3 - "$WEB_RELEASE" "$RELEASE/app/stat.html" "$RELEASE_ID" <<'PY_RELEASE'
from pathlib import Path
import sys
web=Path(sys.argv[1]); stat=Path(sys.argv[2]); release=sys.argv[3]
for path in list(web.iterdir())+[stat]:
    if path.is_file() and path.suffix.lower() in {'.html','.js','.css','.json','.webmanifest'}:
        try: text=path.read_text(encoding='utf-8')
        except UnicodeDecodeError: continue
        if '__BVMAC_RELEASE__' in text:
            path.write_text(text.replace('__BVMAC_RELEASE__',release),encoding='utf-8')
PY_RELEASE
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install --upgrade pip
"$RELEASE/.venv/bin/python" -m pip install -r "$RELEASE/requirements.txt"

# Install the public-data pipeline on a clean server. Existing installations are
# preserved by default unless BVMAC_PIPELINE_POLICY=replace is explicitly set.
PIPELINE_DIR="$APP_BASE/pipeline"
PIPELINE_POLICY="${BVMAC_PIPELINE_POLICY:-auto}"
if [ "$PIPELINE_POLICY" = replace ] || [ ! -f "$PIPELINE_DIR/run.py" ]; then
  install -d -o root -g root -m 0755 "$PIPELINE_DIR"
  for f in bvmac_downloader.py bvmac_extract.py run.py; do
    install -o root -g root -m 0755 "$BASE/pipeline/$f" "$PIPELINE_DIR/$f"
  done
fi
if [ ! -s "$MASTER" ]; then
  echo "[INFO] No master workbook found. Building it from public BVMAC bulletins."
  "$RELEASE/.venv/bin/python" "$PIPELINE_DIR/run.py" --data-dir "$DATA_DIR"
fi
[ -s "$MASTER" ] || die "Master workbook could not be created: $MASTER"
for f in launch_support.py email_auth.py admin_security.py bvmac_api.py common.py auth_module.py market.py portfolio_module.py portfolio_valuation.py feedback_module.py user_tools.py push_module.py push_service.py feed_module.py admin_module.py admin_communications.py weekly_email.py ml_radar.py browser_analytics.py; do
  "$RELEASE/.venv/bin/python" -m py_compile "$RELEASE/app/$f"
done
"$RELEASE/.venv/bin/python" -m py_compile "$RELEASE/ml/infer.py" "$RELEASE/scripts/new_data_push.py" "$RELEASE/scripts/pipeline_watch.py"

log "5/14" "Secrets, encryption and /stat account"
touch "$SECRETS"
python3 - "$SECRETS" "$DOMAIN" "$MASTER" "$MASTER_REPORT" <<'PY'
import base64,secrets,sys
from pathlib import Path
p=Path(sys.argv[1]); domain=sys.argv[2]; master=sys.argv[3]; report=sys.argv[4]
d={}
if p.exists():
    for line in p.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1); d[k]=v
rnd=lambda n=48: secrets.token_urlsafe(n)
d.setdefault('BVMAC_EDITOR_NAME','Independent community project')
d.setdefault('BVMAC_AUTH_PEPPER',rnd(48))
d.setdefault('BVMAC_LOOKUP_PEPPER',rnd(48))
d.setdefault('BVMAC_WEBSTATS_HMAC_SECRET',rnd(48))
d.setdefault('BVMAC_PII_KEY',base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
d['BVMAC_PUBLIC_ORIGIN']=f'https://{domain}'
d['BVMAC_ANALYTICS_HOST']=domain
d['BVMAC_COOKIE_SECURE']='1'
d['BVMAC_MASTER_FILE']=master
d['BVMAC_DOWNLOAD_REPORT']=report
d['BVMAC_PIPELINE_MODE']='external-master'
d.setdefault('BVMAC_USER_SESSION_HOURS','12')
d.setdefault('BVMAC_DEFAULT_PORTFOLIO_AMOUNT','1000000')
p.write_text('\n'.join(f'{k}={v}' for k,v in sorted(d.items()))+'\n',encoding='utf-8')
PY
chown root:"$SECRET_GROUP" "$SECRETS"; chmod 0640 "$SECRETS"
"$RELEASE/.venv/bin/python" "$RELEASE/scripts/generate_vapid.py" --private "$ETC_DIR/vapid_private.pem" --env "$SECRETS" --subject "${BVMAC_VAPID_SUBJECT:-mailto:$LE_EMAIL}" >/dev/null
chown root:"$SECRET_GROUP" "$ETC_DIR/vapid_private.pem" "$SECRETS"
chmod 0640 "$ETC_DIR/vapid_private.pem" "$SECRETS"
if [ ! -s "$STAT_ENV" ] || ! grep -q '^BVMAC_STAT_PASSWORD_HASH=' "$STAT_ENV"; then
  INITIAL_STAT_PASSWORD="$("$RELEASE/.venv/bin/python" "$RELEASE/scripts/set_stat_password.py" --env "$STAT_ENV")"
fi
chown root:"$SECRET_GROUP" "$STAT_ENV"; chmod 0640 "$STAT_ENV"

log "6/14" "PostgreSQL roles, database and schemas"
DB_MUTATION_STARTED=1
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d postgres <<SQL
DO \$\$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$API_ROLE') THEN CREATE ROLE $API_ROLE LOGIN; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$IMPORT_ROLE') THEN CREATE ROLE $IMPORT_ROLE LOGIN; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$STATS_ROLE') THEN CREATE ROLE $STATS_ROLE LOGIN; END IF;
END \$\$;
SELECT 'CREATE DATABASE $DB' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='$DB')\gexec
SQL
sed -e "s/bvmacapi/$API_ROLE/g" -e "s/bvmacimport/$IMPORT_ROLE/g" "$BASE/database/schema.sql" \
  | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB"
sed -e "s/__API_ROLE__/$API_ROLE/g" -e "s/__STATS_ROLE__/$STATS_ROLE/g" "$BASE/database/application.sql" \
  | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB"
cat "$BASE/database/portfolio_workspace.sql" "$BASE/database/notification_inbox.sql" "$BASE/database/weekly_email.sql" "$BASE/database/admin_communications.sql" \
  | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB"
sed -e "s/__API_ROLE__/$API_ROLE/g" -e "s/__IMPORT_ROLE__/$IMPORT_ROLE/g" "$BASE/database/ml_radar.sql" \
  | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -d "$DB" <<SQL
GRANT SELECT,INSERT,UPDATE,DELETE ON auth.notification_inbox,auth.email_preference,admin.weekly_email_run,admin.weekly_email_delivery,admin.communication_campaign,admin.communication_recipient,admin.market_data_push_state TO $API_ROLE;
GRANT USAGE,SELECT ON SEQUENCE auth.notification_inbox_notification_id_seq,admin.weekly_email_run_run_id_seq,admin.weekly_email_delivery_delivery_id_seq,admin.communication_campaign_campaign_id_seq TO $API_ROLE;
SQL

MASTER_DIR="$(dirname "$MASTER")"
PARENT="$MASTER_DIR"
while [ "$PARENT" != "/" ] && [ -n "$PARENT" ]; do
  setfacl -m "u:${IMPORT_ROLE}:--x" "$PARENT" 2>/dev/null || true
  PARENT="$(dirname "$PARENT")"
done
setfacl -m "u:${IMPORT_ROLE}:rx" "$MASTER_DIR" 2>/dev/null || true
setfacl -m "d:u:${IMPORT_ROLE}:rX" "$MASTER_DIR" 2>/dev/null || true
setfacl -m "u:${IMPORT_ROLE}:r" "$MASTER" 2>/dev/null || true

log "7/14" "Initial Excel to PostgreSQL import before cutover"
runuser -u "$IMPORT_ROLE" -- env \
  "BVMAC_IMPORT_DSN=dbname=$DB user=$IMPORT_ROLE host=/var/run/postgresql" \
  "$RELEASE/.venv/bin/python" "$RELEASE/db/import_excel.py" --file "$MASTER"
runuser -u "$IMPORT_ROLE" -- env "BVMAC_IMPORT_DSN=dbname=$DB user=$IMPORT_ROLE host=/var/run/postgresql" \
  "$RELEASE/.venv/bin/python" "$RELEASE/ml/infer.py" --force

log "8/14" "Test the candidate API on temporary port $CANDIDATE_PORT"
cat > "/etc/systemd/system/$CANDIDATE_UNIT" <<UNIT
[Unit]
Description=BVMAC API candidate before cutover
Wants=network-online.target postgresql.service
After=network-online.target postgresql.service

[Service]
Type=simple
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
WorkingDirectory=$RELEASE/app
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_DOWNLOAD_REPORT=$MASTER_REPORT"
Environment="BVMAC_RECENT_DAYS=120"
Environment="BVMAC_STAT_ENV=$STAT_ENV"
Environment="BVMAC_ENV=production"
Environment="BVMAC_SERVICE_PREFIX=$SERVICE_PREFIX"
Environment="BVMAC_API_PORT=$CANDIDATE_PORT"
Environment="BVMAC_APP_DIR=$RELEASE"
Environment="BVMAC_WEB_DIR=$WEB_RELEASE"
Environment="BVMAC_DATA_DIR=$DATA_DIR"
EnvironmentFile=-$SECRETS
EnvironmentFile=-$STAT_ENV
ExecStart=$RELEASE/.venv/bin/python -m uvicorn bvmac_api:app --host 127.0.0.1 --port $CANDIDATE_PORT --workers 1 --loop asyncio --http h11 --no-access-log
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadOnlyPaths=$RELEASE $WEB_RELEASE $MASTER
ReadWritePaths=/run/postgresql
UNIT
systemctl daemon-reload
systemctl start "$CANDIDATE_UNIT"
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$CANDIDATE_PORT/api/health" >/dev/null 2>&1 && break
  sleep 1
done
systemctl is-active --quiet "$CANDIDATE_UNIT" || { systemctl status "$CANDIDATE_UNIT" --no-pager -l || true; journalctl -u "$CANDIDATE_UNIT" -n 120 --no-pager; exit 1; }
curl -fsS "http://127.0.0.1:$CANDIDATE_PORT/api/health" >/dev/null
curl -fsS "http://127.0.0.1:$CANDIDATE_PORT/api/landing" -o /dev/null
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CANDIDATE_PORT/api/public-data")"
[ "$CODE" = 401 ] || die "Full API exposed without authentication (HTTP $CODE)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CANDIDATE_PORT/api/v3/market/companies")"
[ "$CODE" = 401 ] || die "Market API exposed without authentication (HTTP $CODE)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CANDIDATE_PORT/api/v3/auth/me")"
[ "$CODE" = 401 ] || die "Candidate auth/me should return 401, got $CODE"

log "9/14" "Prepare HTTPS and Nginx"
NGINX_CHANGED=1
install -d -o root -g root -m 0755 /etc/nginx/snippets
cat > /etc/nginx/conf.d/bvmac-logformat.conf <<'NGX'
log_format bvmac_webstats_v3 escape=json '{"time":"$time_iso8601","host":"$host","ip":"$remote_addr","method":"$request_method","uri":"$request_uri","status":$status,"request_time":"$request_time","referer":"$http_referer","ua":"$http_user_agent"}';
NGX
rm -f /etc/nginx/conf.d/bvmac-webstats.conf 2>/dev/null || true
cat > /etc/nginx/conf.d/bvmac-limits.conf <<'NGX'
limit_req_zone $binary_remote_addr zone=bvmac_v3_api:2m rate=120r/m;
limit_req_zone $binary_remote_addr zone=bvmac_v3_login:1m rate=8r/m;
limit_req_zone $binary_remote_addr zone=bvmac_v3_forms:1m rate=20r/m;
NGX

touch "$RAW_LOG"
chown root:adm "$RAW_LOG" 2>/dev/null || chown root:root "$RAW_LOG"
chmod 0640 "$RAW_LOG"

write_snippet(){
  local port="$1"
  cat > /etc/nginx/snippets/bvmac-app.inc <<NGX
access_log $RAW_LOG bvmac_webstats_v3;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
limit_req_status 429;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'" always;

location = /api/health {
    auth_request off;
    auth_basic off;
    limit_req zone=bvmac_v3_api burst=20 nodelay;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location = /api/landing {
    auth_request off;
    auth_basic off;
    limit_req zone=bvmac_v3_api burst=20 nodelay;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "public, max-age=30" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'self'" always;
    add_header Strict-Transport-Security "max-age=31536000" always;
}
location = /api/v3/auth/login {
    limit_req zone=bvmac_v3_login burst=2 nodelay;
    client_max_body_size 32k;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location = /api/v3/auth/register {
    limit_req zone=bvmac_v3_forms burst=3 nodelay;
    client_max_body_size 32k;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location = /api/v3/auth/forgot-password {
    limit_req zone=bvmac_v3_forms burst=2 nodelay;
    client_max_body_size 32k;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location ^~ /api/v3/ {
    limit_req zone=bvmac_v3_api burst=40 nodelay;
    client_max_body_size 256k;
    proxy_pass http://127.0.0.1:$port;
    proxy_http_version 1.1;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_connect_timeout 5s; proxy_read_timeout 120s;
    add_header Cache-Control "no-store" always;
}
location ^~ /api/stat/ {
    limit_req zone=bvmac_v3_api burst=20 nodelay;
    client_max_body_size 64k;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location = /_member_access {
    internal;
    proxy_pass http://127.0.0.1:$port/api/access;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr;
}
location = /demo.html {
    auth_request /_member_access;
    error_page 401 403 =302 /auth.html?mode=login;
    add_header Cache-Control "private, no-store" always;
    try_files \$uri =404;
}
location = /auth.html {
    auth_request off;
    auth_basic off;
    add_header Cache-Control "no-store" always;
    try_files \$uri =404;
}
location = /stat {
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
    add_header Cache-Control "no-store" always;
}
location ^~ /api/ {
    limit_req zone=bvmac_v3_api burst=40 nodelay;
    proxy_pass http://127.0.0.1:$port;
    proxy_http_version 1.1;
    proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme;
}
location = /sw.js {
    default_type application/javascript;
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;
    try_files \$uri =404;
}
location = /manifest.webmanifest {
    default_type application/manifest+json;
    add_header Cache-Control "public, max-age=3600" always;
    try_files \$uri =404;
}
location ~* \.(?:xlsx?|csv|pdf|pkl|pickle|py|sh|json|part)$ { return 404; }
location ~ /\. { deny all; }
location / { try_files \$uri \$uri/ =404; }
NGX
  python3 "$BASE/operations/patch_nginx_app_routes.py" /etc/nginx/snippets/bvmac-app.inc
}

write_site(){
  local root="$1" mode="$2"
  if [ "$mode" = http ]; then
    cat > "$SITE" <<NGX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    root $root;
    index index.html;
    location ^~ /.well-known/acme-challenge/ { root $root; try_files \$uri =404; }
    include /etc/nginx/snippets/bvmac-app.inc;
}
NGX
  else
    cat > "$SITE" <<NGX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    root $root;
    location ^~ /.well-known/acme-challenge/ { root $root; try_files \$uri =404; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN;
    root $root;
    index index.html;
    ssl_certificate $CERT_FULLCHAIN;
    ssl_certificate_key $CERT_KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:bvmac_v3_SSL:10m;
    ssl_session_timeout 1d;
    add_header Strict-Transport-Security "max-age=31536000" always;
    include /etc/nginx/snippets/bvmac-app.inc;
}
NGX
  fi
  ln -sfn "$SITE" "$SITE_LINK"
  nginx -t
  systemctl reload nginx
  NGINX_CHANGED=1
}

# If no certificate exists yet, expose only the HTTP challenge after candidate API validation.
if [ ! -s "$CERT_FULLCHAIN" ] || [ ! -s "$CERT_KEY" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  write_snippet "$CANDIDATE_PORT"
  write_site "$WEB_RELEASE" http
  mkdir -p "$WEB_RELEASE/.well-known/acme-challenge"
  certbot certonly --webroot -w "$WEB_RELEASE" -d "$DOMAIN" \
    --email "$LE_EMAIL" --agree-tos --no-eff-email --non-interactive
fi
[ -s "$CERT_FULLCHAIN" ] && [ -s "$CERT_KEY" ] || die "HTTPS certificate not found after Certbot"

log "10/14" "Protected cutover through the candidate API"
CUTOVER_STARTED=1
write_snippet "$CANDIDATE_PORT"
write_site "$WEB_RELEASE" https

# Test the freshly loaded Nginx vhost without depending on public DNS,
# DNS caches or an external network path. --resolve keeps the
# vrai hostname pour SNI/TLS et Host, mais force la connexion sur le Nginx local.
https_local_code(){
  local path="$1"
  curl -sS --connect-timeout 5 --max-time 20 \
    --resolve "$DOMAIN:443:127.0.0.1" \
    -o /dev/null -w '%{http_code}' "https://$DOMAIN$path" || true
}
expect_https_code(){
  local path="$1" expected="$2" label="$3" code direct
  code="$(https_local_code "$path")"
  if [ "$code" != "$expected" ]; then
    direct="$(curl -sS --connect-timeout 3 --max-time 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CANDIDATE_PORT$path" || true)"
    echo "[DIAGNOSTIC] $label : Nginx local=${code:-000}, API candidate directe=${direct:-000}, attendu=$expected" >&2
    die "$label via Nginx local: HTTP ${code:-000}, attendu $expected"
  fi
  echo "[OK] $label via Nginx local (HTTP $code)"
}

expect_https_code "/api/health" "200" "API health candidate"
expect_https_code "/api/landing" "200" "Landing publique candidate"
expect_https_code "/api/public-data" "401" "Full data API protected"
expect_https_code "/api/v3/market/companies" "401" "Market API protected"
expect_https_code "/auth.html?mode=login" "200" "Page de connexion publique"
expect_https_code "/app" "302" "Authenticated app protected"
expect_https_code "/app?view=analysis" "302" "Analysis protected under /app"
expect_https_code "/app?view=portfolios" "302" "Portfolios protected under /app"

log "11/14" "Install production services and start API on $API_PORT"
ln -sfn "$RELEASE" "$APP_CURRENT"
ln -sfn "$WEB_RELEASE" "$WEB_CURRENT"

cat > "/etc/systemd/system/$API_UNIT" <<UNIT
[Unit]
Description=BVMAC API (production)
Wants=network-online.target postgresql.service
After=network-online.target postgresql.service

[Service]
Type=simple
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
WorkingDirectory=$APP_CURRENT/app
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_DOWNLOAD_REPORT=$MASTER_REPORT"
Environment="BVMAC_RECENT_DAYS=120"
Environment="BVMAC_STAT_ENV=$STAT_ENV"
Environment="BVMAC_ENV=production"
Environment="BVMAC_SERVICE_PREFIX=$SERVICE_PREFIX"
Environment="BVMAC_API_PORT=$API_PORT"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
Environment="BVMAC_WEB_DIR=$WEB_CURRENT"
Environment="BVMAC_DATA_DIR=$DATA_DIR"
EnvironmentFile=-$SECRETS
EnvironmentFile=-$STAT_ENV
ExecStart=$APP_CURRENT/.venv/bin/python -m uvicorn bvmac_api:app --host 127.0.0.1 --port $API_PORT --workers 1 --loop asyncio --http h11 --no-access-log
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadOnlyPaths=$APP_CURRENT $WEB_CURRENT $MASTER
ReadWritePaths=/run/postgresql

[Install]
WantedBy=multi-user.target
UNIT

cat > "/etc/systemd/system/$IMPORT_UNIT" <<UNIT
[Unit]
Description=BVMAC Excel to PostgreSQL import (production)
Wants=postgresql.service
After=postgresql.service

[Service]
Type=oneshot
User=$IMPORT_ROLE
Group=$DATA_GROUP
WorkingDirectory=$APP_CURRENT
Environment="BVMAC_IMPORT_DSN=dbname=$DB user=$IMPORT_ROLE host=/var/run/postgresql"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/db/import_excel.py --file $MASTER
ExecStartPost=-$APP_CURRENT/.venv/bin/python $APP_CURRENT/ml/infer.py
ExecStartPost=-/usr/bin/touch /var/lib/bvmac/events/import-completed
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=6
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadOnlyPaths=$APP_CURRENT $MASTER
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$IMPORT_PATH" <<UNIT
[Unit]
Description=BVMAC watches the canonical Excel workbook (production)

[Path]
PathChanged=$MASTER
Unit=$IMPORT_UNIT

[Install]
WantedBy=multi-user.target
UNIT

cat > "/etc/systemd/system/$WEBSTATS_UNIT" <<UNIT
[Unit]
Description=BVMAC pseudonymizes and imports Nginx logs (production)
After=nginx.service postgresql.service
Wants=nginx.service postgresql.service

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=-$SECRETS
Environment="BVMAC_APP_DIR=$APP_CURRENT"
Environment="BVMAC_WEBSTATS_DSN=dbname=$DB user=$STATS_ROLE host=/var/run/postgresql"
Environment="BVMAC_WEBSTATS_USER=$STATS_ROLE"
Environment="BVMAC_WEBSTATS_RAW=$RAW_LOG"
Environment="BVMAC_WEBSTATS_SPOOL=$SPOOL_DIR"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/webstats_import_nginx.py
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/var/log/nginx $SPOOL_DIR /run/postgresql
UNIT

cat > "/etc/systemd/system/$WEBSTATS_TIMER" <<UNIT
[Unit]
Description=BVMAC imports Nginx logs every 5 minutes (production)
[Timer]
OnCalendar=*-*-* *:00/5:00
AccuracySec=30s
Persistent=true
Unit=$WEBSTATS_UNIT
[Install]
WantedBy=timers.target
UNIT

cat > "/etc/systemd/system/$ALERTS_UNIT" <<UNIT
[Unit]
Description=BVMAC evaluates user alerts (production)
After=postgresql.service $IMPORT_UNIT
Wants=postgresql.service

[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/alerts_evaluate.py
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$ALERTS_TIMER" <<UNIT
[Unit]
Description=BVMAC evaluates alerts every 10 minutes (production)
[Timer]
OnCalendar=*-*-* *:00/10:00
AccuracySec=30s
Persistent=true
Unit=$ALERTS_UNIT
[Install]
WantedBy=timers.target
UNIT

cat > "/etc/systemd/system/$FEEDS_UNIT" <<UNIT
[Unit]
Description=BVMAC official RSS feed watcher (production)
After=network-online.target postgresql.service
Wants=network-online.target postgresql.service
[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/feed_collector.py
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$APP_CURRENT
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$FEEDS_TIMER" <<UNIT
[Unit]
Description=BVMAC checks RSS feeds every 15 minutes (production)
[Timer]
OnCalendar=*-*-* *:00/15:00
AccuracySec=30s
Persistent=true
Unit=$FEEDS_UNIT
[Install]
WantedBy=timers.target
UNIT

cat > "/etc/systemd/system/$NOTIF_UNIT" <<UNIT
[Unit]
Description=BVMAC communications and platform updates (production)
After=postgresql.service
Wants=postgresql.service
[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/notifications_dispatch.py --mode broadcasts
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$APP_CURRENT $ETC_DIR
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$NOTIF_TIMER" <<UNIT
[Unit]
Description=BVMAC processes pending communications every hour (production)
[Timer]
OnCalendar=*-*-* *:00:00
AccuracySec=2min
Persistent=true
Unit=$NOTIF_UNIT
[Install]
WantedBy=timers.target
UNIT

cat > "/etc/systemd/system/$WEEKLY_UNIT" <<UNIT
[Unit]
Description=BVMAC weekly market digest (production)
After=postgresql.service
Wants=postgresql.service
[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/notifications_dispatch.py --mode weekly
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$APP_CURRENT $ETC_DIR
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$WEEKLY_TIMER" <<UNIT
[Unit]
Description=BVMAC weekly digest every Monday at 08:00 Douala time (production)
[Timer]
OnCalendar=Mon *-*-* 08:00:00 Africa/Douala
AccuracySec=1min
Persistent=true
Unit=$WEEKLY_UNIT
[Install]
WantedBy=timers.target
UNIT

cat > "/etc/systemd/system/$WEEKLY_EMAIL_UNIT" <<UNIT
[Unit]
Description=BVMAC weekly email report (production)
After=postgresql.service
Wants=postgresql.service
[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/notifications_dispatch.py --mode weekly-email
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$APP_CURRENT $ETC_DIR
ReadWritePaths=/run/postgresql
UNIT

cat > "/etc/systemd/system/$WEEKLY_EMAIL_TIMER" <<UNIT
[Unit]
Description=BVMAC weekly email every Monday at 09:00 Douala time (production)
[Timer]
OnCalendar=Mon *-*-* 09:00:00 Africa/Douala
AccuracySec=1min
Persistent=true
Unit=$WEEKLY_EMAIL_UNIT
[Install]
WantedBy=timers.target
UNIT

install -d -o $IMPORT_ROLE -g $DATA_GROUP -m 0770 /var/lib/bvmac/events
runuser -u postgres -- psql -d "$DB" -v ON_ERROR_STOP=1 -q <<'SQL'
INSERT INTO admin.market_data_push_state(singleton,last_import_id,last_data_date_id,updated_at)
SELECT true,
       (SELECT import_id FROM market.import_batch WHERE status='completed' ORDER BY import_id DESC LIMIT 1),
       (SELECT max((data->>'bulletin_date_id')::int) FROM market.current_excel_row WHERE sheet_name='fact_prices' AND data ? 'bulletin_date_id'),
       now()
ON CONFLICT(singleton) DO NOTHING;
SQL
cat > /etc/systemd/system/$NEW_DATA_UNIT <<UNIT
[Unit]
Description=BVMAC push after new market data import
After=postgresql.service $IMPORT_UNIT
Wants=postgresql.service
[Service]
Type=oneshot
User=$API_ROLE
Group=$DATA_GROUP
SupplementaryGroups=$SECRET_GROUP
EnvironmentFile=-$SECRETS
Environment="BVMAC_DB_DSN=dbname=$DB user=$API_ROLE host=/var/run/postgresql"
Environment="BVMAC_APP_DIR=$APP_CURRENT"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/scripts/new_data_push.py
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=$APP_CURRENT $ETC_DIR /var/lib/bvmac/events
ReadWritePaths=/run/postgresql
UNIT
cat > /etc/systemd/system/$NEW_DATA_PATH <<UNIT
[Unit]
Description=BVMAC detects completed market-data imports
[Path]
PathChanged=/var/lib/bvmac/events/import-completed
Unit=$NEW_DATA_UNIT
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/$PIPELINE_WATCH_UNIT <<UNIT
[Unit]
Description=BVMAC lightweight new BOC detection
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/systemd/system/bvmac-pipeline.service
[Service]
Type=oneshot
User=root
Environment="BVMAC_BULLETIN_DIR=/var/lib/bvmac/bulletins"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_CURRENT/scripts/pipeline_watch.py
TimeoutStartSec=90
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
UNIT
cat > /etc/systemd/system/$PIPELINE_WATCH_TIMER <<UNIT
[Unit]
Description=BVMAC checks for a new bulletin every 30 minutes
[Timer]
OnCalendar=*-*-* *:00/30:00
AccuracySec=1min
Persistent=true
Unit=$PIPELINE_WATCH_UNIT
[Install]
WantedBy=timers.target
UNIT

# Install the daily pipeline service on clean servers. Existing custom pipeline units
# are preserved unless the operator explicitly chooses --pipeline replace.
if [ "$PIPELINE_POLICY" = replace ] || [ ! -f /etc/systemd/system/bvmac-pipeline.service ]; then
cat > /etc/systemd/system/bvmac-pipeline.service <<UNIT
[Unit]
Description=BVMAC public-data pipeline
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=root
Environment="BVMAC_DATA_DIR=$DATA_DIR"
ExecStart=$APP_CURRENT/.venv/bin/python $APP_BASE/pipeline/run.py --data-dir $DATA_DIR
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR /run/lock
UNIT
cat > /etc/systemd/system/bvmac-pipeline.timer <<UNIT
[Unit]
Description=BVMAC daily public-data pipeline
[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
fi

# The old analytics-v2 maintenance unit is obsolete.
systemctl disable --now bvmac-analytics-maintenance.timer bvmac-analytics-maintenance.service 2>/dev/null || true
rm -f /etc/systemd/system/bvmac-analytics-maintenance.timer /etc/systemd/system/bvmac-analytics-maintenance.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable "$API_UNIT" "$IMPORT_PATH" "$WEBSTATS_TIMER" "$ALERTS_TIMER" "$FEEDS_TIMER" "$NOTIF_TIMER" "$WEEKLY_TIMER" "$WEEKLY_EMAIL_TIMER" "$NEW_DATA_PATH" bvmac-pipeline.timer
if [ -f /etc/systemd/system/bvmac-pipeline.service ]; then systemctl enable "$PIPELINE_WATCH_TIMER"; fi
systemctl restart "$API_UNIT"
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1 && break
  sleep 1
done
systemctl is-active --quiet "$API_UNIT" || { systemctl status "$API_UNIT" --no-pager -l || true; journalctl -u "$API_UNIT" -n 120 --no-pager; exit 1; }
curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null
curl -fsS "http://127.0.0.1:$API_PORT/api/landing" -o /dev/null
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$API_PORT/api/public-data")"
[ "$CODE" = 401 ] || die "Full API exposed without authentication (HTTP $CODE)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$API_PORT/api/v3/market/companies")"
[ "$CODE" = 401 ] || die "Market API exposed without authentication (HTTP $CODE)"

# Re-import just before enabling the watcher in case the pipeline updated Excel during deployment.
systemctl start "$IMPORT_UNIT"
systemctl start "$IMPORT_PATH" "$WEBSTATS_TIMER" "$ALERTS_TIMER" "$FEEDS_TIMER" "$NOTIF_TIMER" "$WEEKLY_TIMER" "$WEEKLY_EMAIL_TIMER" "$NEW_DATA_PATH" bvmac-pipeline.timer
if [ -f /etc/systemd/system/bvmac-pipeline.service ]; then systemctl start "$PIPELINE_WATCH_TIMER"; fi
# Silent RSS bootstrap: existing entries are stored without sending push notifications.
systemctl start "$FEEDS_UNIT" || echo "[WARN] RSS is temporarily unavailable; the timer will retry."
runuser -u postgres -- psql -d "$DB" -v ON_ERROR_STOP=1 -qAt <<'SQL'
INSERT INTO admin.notification_broadcast(kind,title,body,target_url,created_by)
SELECT 'platform_update','BVMAC Market','Platform updated','/app','deploy.sh community'
WHERE NOT EXISTS (SELECT 1 FROM admin.notification_broadcast WHERE kind='platform_update' AND created_by='deploy.sh community');
SQL
systemctl start "$NOTIF_UNIT" || true

log "12/14" "Final Nginx cutover to the API and frontend"
write_snippet "$API_PORT"
write_site "$WEB_CURRENT" https
curl -fsS "https://$DOMAIN/" -o /dev/null
curl -fsS "https://$DOMAIN/api/health" >/dev/null
curl -fsS "https://$DOMAIN/api/landing" -o /dev/null
curl -fsS "https://$DOMAIN/manifest.webmanifest" -o /dev/null
curl -fsS "https://$DOMAIN/sw.js" -o /dev/null
curl -fsS "https://$DOMAIN/auth.html?mode=login" -o /dev/null
CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/public-data")"
[ "$CODE" = 401 ] || die "Full HTTPS API exposed without authentication (HTTP $CODE)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/v3/market/companies")"
[ "$CODE" = 401 ] || die "Market API exposed without authentication (HTTP $CODE)"
for PRIVATE_PAGE in "/app?view=home" "/app?view=radar" "/app?view=analysis" "/app?view=portfolios" "/app?view=feeds"; do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN$PRIVATE_PAGE")"
  [ "$CODE" = 302 ] || die "$PRIVATE_PAGE should redirect before rendering without a session (HTTP $CODE)"
done
CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/v3/auth/me")"
[ "$CODE" = 401 ] || die "auth/me should return 401, got $CODE"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/stat/me")"
[ "$CODE" = 401 ] || die "stat/me should return 401, got $CODE"

python3 - "$WEB_RELEASE" "$DOMAIN" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]);origin='https://'+sys.argv[2]
for name in ('index.html','information.html','app.html','app-analysis.html','app-portfolios.html','app-feeds.html','auth.html'):
    p=root/name;p.write_text(p.read_text().replace('__PUBLIC_ORIGIN__',origin))
(root/'robots.txt').write_text('User-agent: *\nAllow: /$\nAllow: /information.html$\nDisallow: /stat\nDisallow: /api/\nDisallow: /app\nDisallow: /auth.html\nDisallow: /demo.html\nSitemap: '+origin+'/sitemap.xml\n')
(root/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+origin+path+'</loc></url>' for path in ('/','/information.html'))+'</urlset>')
PY

log "13/14" "Frontend, service and pipeline integrity checks"
systemctl start "$WEBSTATS_UNIT" || { journalctl -u "$WEBSTATS_UNIT" -n 120 --no-pager; exit 1; }
systemctl start "$ALERTS_UNIT" || { journalctl -u "$ALERTS_UNIT" -n 120 --no-pager; exit 1; }
grep -q 'analytics.js' "$WEB_CURRENT/index.html" || die "analytics.js missing from index.html"
if grep -RIEqi --include='*.html' --include='*.js' 'fonts\.googleapis|fonts\.gstatic|googletagmanager|google-analytics|facebook\.net|hotjar|mixpanel|segment\.com' "$WEB_CURRENT"; then
  die "Third-party analytics/font call detected"
fi
nginx -t
pipeline_manifest > "$BACKUP/pipeline.after.sha256"
if [ "$PIPELINE_POLICY" != replace ] && grep -qv '^MISSING' "$BACKUP/pipeline.before.sha256"; then
  cmp -s "$BACKUP/pipeline.before.sha256" "$BACKUP/pipeline.after.sha256" || die "Existing pipeline changed unexpectedly"
fi
PIPE_ENABLED_BEFORE="$(head -n 1 "$BACKUP/pipeline.timer.enabled" 2>/dev/null || true)"
if [ "$PIPE_ENABLED_BEFORE" = "enabled" ]; then
  systemctl is-enabled --quiet bvmac-pipeline.timer || die "The pipeline timer was enabled before deployment and is no longer enabled"
  systemctl is-active --quiet bvmac-pipeline.timer || die "The pipeline timer was active before deployment and is no longer active"
fi

# The candidate service is no longer needed after final traffic has been verified.
systemctl disable --now "$CANDIDATE_UNIT" 2>/dev/null || true
rm -f "/etc/systemd/system/$CANDIDATE_UNIT"
systemctl daemon-reload

# Previous releases are intentionally kept. Git and deployment backups provide history.

install_operations "$APP_CURRENT" "$ETC_DIR" "$DB" "$API_PORT" "$SERVICE_PREFIX"

# Keep a standalone checker on the VPS for future diagnostics.
install -o root -g root -m 0755 "$BASE/checking.sh" "$APP_BASE/checking.sh"

# Blocking production checks run while rollback is still armed.
BVMAC_DOMAIN="$DOMAIN" bash "$BASE/checking.sh" --server

CUTOVER_STARTED=0
trap - ERR

log "14/14" "Deployment completed"
cat <<EOF2
============================================================
 BVMAC Market installed successfully
============================================================
 Site               : https://$DOMAIN/
 Analysis            : https://$DOMAIN/app?view=analysis
 Portfolios/account  : https://$DOMAIN/app?view=portfolios
 Sign-in/register    : https://$DOMAIN/auth.html?mode=login
 Administration     : https://$DOMAIN/stat
 PostgreSQL         : $DB
 API locale         : 127.0.0.1:$API_PORT
 Master             : $MASTER
 Release backend    : $RELEASE
 Release frontend   : $WEB_RELEASE
 Rollback backup    : $BACKUP
 Production pipeline: PRESERVED, unchanged
 Test environment    : REMOVED
 Let's Encrypt      : $LE_EMAIL
============================================================
EOF2
if [ -n "$INITIAL_STAT_PASSWORD" ]; then
  cat <<EOF2
INITIAL /stat PASSWORD: $INITIAL_STAT_PASSWORD
Save it now; only its hash is stored.
============================================================
EOF2
else
  echo "Existing /stat password preserved."
fi
echo "Reset /stat password: sudo $APP_CURRENT/.venv/bin/python $APP_CURRENT/scripts/set_stat_password.py --env $STAT_ENV --password 'NEW_PASSWORD'"
