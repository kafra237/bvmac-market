#!/usr/bin/env bash
set -Eeuo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage(){ cat <<'EOF'
BVMAC Market production checks

Local/remote mode:
  ./checking.sh [--host HOST] [--user USER] [--domain DOMAIN] [--ssh-port PORT]

Server mode:
  sudo BVMAC_DOMAIN=market.example.org bash /opt/bvmac/checking.sh --server
EOF
}
if [ "${1:-}" != "--server" ]; then
  HOST=""; SSH_USER=""; DOMAIN=""; SSH_PORT="22"
  while [ $# -gt 0 ]; do
    case "$1" in
      --host) HOST="${2:-}"; shift 2;;
      --user) SSH_USER="${2:-}"; shift 2;;
      --domain) DOMAIN="${2:-}"; shift 2;;
      --ssh-port) SSH_PORT="${2:-}"; shift 2;;
      -h|--help) usage; exit 0;;
      *) echo "Unknown option: $1" >&2; usage; exit 2;;
    esac
  done
  [ -n "$HOST" ] || read -r -p "VPS host or IP: " HOST
  [ -n "$SSH_USER" ] || { read -r -p "SSH user [ubuntu]: " SSH_USER; SSH_USER="${SSH_USER:-ubuntu}"; }
  [ -n "$DOMAIN" ] || read -r -p "Public domain: " DOMAIN
  [[ "$HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || { echo "Invalid VPS host." >&2; exit 2; }
  [[ "$SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || { echo "Invalid SSH user." >&2; exit 2; }
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid domain." >&2; exit 2; }
  [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || { echo "Invalid SSH port." >&2; exit 2; }
  q_domain=$(printf '%q' "$DOMAIN")
  ssh -tt -p "$SSH_PORT" "$SSH_USER@$HOST" "sudo env BVMAC_DOMAIN=$q_domain bash /opt/bvmac/checking.sh --server"
  exit $?
fi
shift

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN="${BVMAC_DOMAIN:?BVMAC_DOMAIN is required}"
APP="$(readlink -f /opt/bvmac/current 2>/dev/null || true)"
WEB="$(readlink -f /var/www/bvmac-current 2>/dev/null || true)"
DB="bvmac"
SNIPPET="/etc/nginx/snippets/bvmac-app.inc"
GREEN='\033[1;32m'; RED='\033[1;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
[ "$(id -u)" -eq 0 ] || { echo '[FAIL] Run checking.sh with sudo/root.' >&2; exit 1; }
OK=0; FAIL=0; WARN=0
ok(){ printf "${GREEN}[OK]${NC}   %s\n" "$*"; OK=$((OK+1)); }
failmsg(){ printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; FAIL=$((FAIL+1)); }
warn(){ printf "${YELLOW}[WARN]${NC} %s\n" "$*"; WARN=$((WARN+1)); }
check(){ local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else failmsg "$name"; fi; }
contains(){ grep -Fq -- "$2" "$1"; }
not_contains(){ ! grep -Fq -- "$2" "$1"; }
https_code(){ curl --resolve "$DOMAIN:443:127.0.0.1" -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://$DOMAIN$1"; }
expect_https(){ local path="$1" expected="$2" label="$3" code; code="$(https_code "$path" || true)"; [ "$code" = "$expected" ] && ok "$label" || failmsg "$label (HTTP $code, expected $expected)"; }

check "API service active" systemctl is-active --quiet bvmac-api.service
check "Nginx config" nginx -t
check "Local API health" curl -fsS --max-time 8 http://127.0.0.1:8765/api/health
if ss -ltnp 2>/dev/null | grep -Eq '127\.0\.0\.1:8765\b' && ! ss -ltn 2>/dev/null | grep -Eq '(0\.0\.0\.0|\[::\]):8765\b'; then ok "Backend bound to localhost only"; else failmsg "Backend exposure on port 8765"; fi
check "PostgreSQL" runuser -u postgres -- psql -d "$DB" -qAt -c 'SELECT 1'
check "Current market import" runuser -u postgres -- psql -d "$DB" -qAt -c 'SELECT import_id FROM market.current_import LIMIT 1'

[ -f "$SNIPPET" ] && ok "BVMAC Nginx snippet present" || failmsg "BVMAC Nginx snippet missing"
contains "$SNIPPET" 'BVMAC_APP_ROUTES_BEGIN' && ok "Canonical /app routing installed" || failmsg "Canonical /app routing missing"
contains "$SNIPPET" 'try_files /app.html =404;' && contains "$SNIPPET" 'if ($arg_view = feeds)' && contains "$SNIPPET" 'if ($arg_view = analysis)' && contains "$SNIPPET" 'if ($arg_view = portfolios)' && contains "$SNIPPET" 'location = /_bvmac_app_feeds' && ok "View routing under /app" || failmsg "View routing rule missing"
contains "$SNIPPET" 'location = /version.json' && ok "Release discovery no-cache route" || failmsg "version.json route missing"

expect_https '/auth.html?mode=login' '200' 'Public sign-in page'
for spec in 'home:Home' 'overview:Overview' 'stories:Stories' 'stocks:Stocks' 'funds:Funds' 'index:Index' 'radar:Radar' 'watchlist:Watchlist' 'data:Data' 'feeds:Feeds' 'analysis:Analysis' 'portfolios:Portfolios'; do IFS=: read -r view label <<<"$spec"; expect_https "/app?view=$view" '302' "$label protected under /app"; done
for spec in '/app.html?view=radar:308' '/feeds.html:308' '/lab.html:308' '/account.html:308'; do path="${spec%:*}"; expected="${spec##*:}"; expect_https "$path" "$expected" "Legacy route redirects: $path"; done
for f in app-feeds.html app-analysis.html app-portfolios.html; do expect_https "/$f" '404' "Internal view document hidden: $f"; done
expect_https '/version.json' '200' 'Release version endpoint'
expect_https '/manifest.webmanifest' '200' 'PWA manifest'
expect_https '/sw.js' '200' 'PWA service worker'

if [ -d "$WEB" ]; then
  [ -f "$WEB/app.html" ] && [ -f "$WEB/app-feeds.html" ] && [ -f "$WEB/app-analysis.html" ] && [ -f "$WEB/app-portfolios.html" ] && ok "All private app view documents installed" || failmsg "Private app view document missing"
  [ ! -e "$WEB/account.html" ] && [ ! -e "$WEB/lab.html" ] && [ ! -e "$WEB/feeds.html" ] && ok "Legacy private HTML files removed" || failmsg "Legacy private HTML file still installed"
  RELEASE_ID="$(python3 - "$WEB/version.json" <<'PY_RELEASE' 2>/dev/null || true
import json,sys
try: print(json.load(open(sys.argv[1],encoding='utf-8')).get('release',''))
except Exception: pass
PY_RELEASE
)"
  if [ -n "$RELEASE_ID" ] && [ "$RELEASE_ID" != '__BVMAC_RELEASE__' ]; then ok "Runtime release identifier: $RELEASE_ID"; else failmsg "Runtime release identifier missing"; fi
  contains "$WEB/app.html" "/shell.js?v=$RELEASE_ID" && ok "Main app assets use current release token" || failmsg "Main app shell token is stale"
  contains "$WEB/app-portfolios.html" "/i18n.js?v=$RELEASE_ID" && contains "$WEB/app-analysis.html" "/i18n.js?v=$RELEASE_ID" && contains "$WEB/app-feeds.html" "/i18n.js?v=$RELEASE_ID" && ok "Translator loaded on all private subviews" || failmsg "Translator missing on a private subview"
  contains "$WEB/pwa.js" "const RELEASE='$RELEASE_ID'" && contains "$WEB/pwa.js" "version.json" && contains "$WEB/pwa.js" "registration.update" && ok "Browser/PWA release auto-update logic" || failmsg "PWA release auto-update incomplete"
  contains "$WEB/sw.js" "const RELEASE='$RELEASE_ID'" && contains "$WEB/sw.js" "SKIP_WAITING" && contains "$WEB/sw.js" "client.navigate(client.url)" && ok "Service worker release activation" || failmsg "Service worker update protocol missing"
  python3 - "$WEB/manifest.webmanifest" <<'PY' >/dev/null 2>&1 && ok "Manifest /app coherence" || failmsg "Manifest /app coherence"
import json,sys
m=json.load(open(sys.argv[1],encoding='utf-8'))
assert m['id']=='/app' and m['start_url'].startswith('/app?view=home') and m['scope']=='/'
PY
else failmsg "Frontend release path missing: $WEB"; fi

# Protected API surfaces.
for path in /api/v3/ml/radar /api/v3/portfolio /api/stat/communications/recipients; do code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:8765$path" || true)"; case "$code" in 401|403|405) ok "Protected API $path";; *) failmsg "Protected API $path returned $code";; esac; done
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 -H 'Content-Type: application/json' -X POST --data '{"user_ids":[1]}' http://127.0.0.1:8765/api/stat/communications/weekly/send || true)"
case "$code" in 401|403) ok "Targeted weekly summary endpoint protected";; *) failmsg "Targeted weekly summary endpoint returned $code";; esac

# Transactional regression check for incident ba5b1f88: no row persists.
if runuser -u postgres -- psql -d "$DB" -v ON_ERROR_STOP=1 -qAt <<'SQL' >/tmp/bvmac-checking-campaign.log 2>&1
BEGIN;
SET LOCAL ROLE bvmacapi;
WITH c AS (
 INSERT INTO admin.communication_campaign(kind,target_url,requested_by,recipient_count)
 VALUES('weekly_email','/app?view=radar','checking',1) RETURNING campaign_id
), u AS (SELECT user_id FROM auth.user_account ORDER BY user_id LIMIT 1)
INSERT INTO admin.communication_recipient(campaign_id,user_id)
SELECT c.campaign_id,u.user_id FROM c CROSS JOIN u;
ROLLBACK;
SQL
then ok "Targeted weekly campaign database path"; else failmsg "Targeted weekly campaign database path (see /tmp/bvmac-checking-campaign.log)"; fi

# Radar / ML remains operational.
if runuser -u postgres -- psql -d "$DB" -qAt -c "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='ml' AND table_name='prediction_run')" | grep -qx t; then ok "Radar ML tables"; else failmsg "Radar ML tables"; fi
if runuser -u postgres -- psql -d "$DB" -qAt -c 'SELECT 1 FROM ml.prediction_run WHERE status='"'"'completed'"'"' ORDER BY run_id DESC LIMIT 1' | grep -qx 1; then ok "Radar ML successful run"; else failmsg "Radar ML successful run"; fi

# Key timers/watchers remain armed.
timer_armed(){ local t="$1" next i; systemctl is-active --quiet "$t" || return 1; for i in 1 2 3 4 5; do next="$(systemctl show "$t" -p NextElapseUSecRealtime --value 2>/dev/null || true)"; [ -n "$next" ] && return 0; sleep 1; done; return 1; }
for t in bvmac-pipeline-watch.timer bvmac-weekly-email.timer bvmac-webstats.timer bvmac-alerts.timer bvmac-notifications.timer; do timer_armed "$t" && ok "$t armed" || failmsg "$t has no next execution"; done
check "New-market-data push watcher" systemctl is-active --quiet bvmac-new-data-push.path

# Build-time browser QA runs in GitHub Actions. Production checking verifies
# that the responsive and bilingual assets are the ones actually deployed.
if contains "$WEB/shell.css" '@media' && contains "$WEB/radar.css" '@media' && contains "$WEB/app.html" 'viewport-fit=cover'; then ok "Responsive assets deployed"; else failmsg "Responsive assets incomplete"; fi
if contains "$WEB/i18n.js" 'bvmac:language-changed' && contains "$WEB/i18n.js" 'Predictive radar'; then ok "Bilingual EN/FR layer deployed"; else failmsg "Bilingual layer incomplete"; fi

check "Pipeline service installed" systemctl cat bvmac-pipeline.service
timer_armed bvmac-pipeline.timer && ok "bvmac-pipeline.timer armed" || failmsg "bvmac-pipeline.timer has no next execution"
[ -s /var/lib/bvmac/output/bvmac_master.xlsx ] && ok "Master workbook present" || failmsg "Master workbook missing"

printf '\n============================================================\n'
if [ "$FAIL" -eq 0 ]; then printf "${GREEN} CHECKING : PASS${NC}\n"; else printf "${RED} CHECKING : FAIL${NC}\n"; fi
printf ' Checks : %d OK / %d FAIL / %d WARN\n' "$OK" "$FAIL" "$WARN"
printf '============================================================\n'
[ "$FAIL" -eq 0 ]
