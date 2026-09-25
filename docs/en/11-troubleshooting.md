# Troubleshooting

Start with `./checking.sh`; it is designed to tell you which layer is failing.

Useful server commands include `systemctl status bvmac-api.service`, `systemctl list-timers --all | grep bvmac`, `journalctl -u bvmac-api.service -n 100 --no-pager`, `nginx -t`, and a PostgreSQL `SELECT` against `market.current_import` or `ml.prediction_run`.

When a browser looks different from another device after deployment, check `/version.json` and the Service Worker before assuming the HTML is wrong. When market data is stale, check the pipeline timer, the 30-minute watcher, the master workbook timestamp and the latest PostgreSQL import in that order.
