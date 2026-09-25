# Dépannage

Commencer par `./checking.sh` : le script indique quelle couche pose problème.

Sur le VPS, les commandes les plus utiles sont `systemctl status bvmac-api.service`, `systemctl list-timers --all | grep bvmac`, `journalctl -u bvmac-api.service -n 100 --no-pager` et `nginx -t`.

Si deux appareils montrent des interfaces différentes après une mise à jour, vérifier `/version.json` et le Service Worker avant de conclure à un problème HTML. Si les données semblent anciennes, contrôler dans l’ordre le timer du pipeline, le watcher 30 minutes, la date du classeur maître puis le dernier import PostgreSQL.
