# Deployment and operations

BVMAC Market exposes only two shell commands at the repository root: `deploy.sh` and `checking.sh`. The goal is to keep production work understandable: one command changes the server, the other only checks it.

## Before you start

You need a VPS running an Ubuntu/Debian-style Linux distribution, a DNS name pointing to it, an SSH account with `sudo`, and ports 80/443 reachable from the Internet. The deployment uses Nginx, PostgreSQL, Python and systemd. On your own computer you need `bash`, `ssh`, `scp` and `tar`. Windows users can run the commands from WSL or Git Bash.

Do not put passwords in the repository. SSH asks for your password or uses your SSH key. Application secrets are created or stored on the server under `/etc/bvmac/`.

## Guided deployment

From the repository root:

```bash
./deploy.sh
```

The script asks for:

- the VPS hostname or IP address;
- the SSH user (for example `ubuntu`);
- the public domain that will host BVMAC Market;
- an administrator / Let's Encrypt email;
- whether an existing custom data pipeline must be preserved (`auto`, the safe default) or replaced by the repository copy (`replace`).

You can also provide these values non-interactively:

```bash
./deploy.sh \
  --host 203.0.113.10 \
  --user ubuntu \
  --domain market.example.org \
  --email admin@example.org \
  --pipeline auto
```

The script never stores the SSH password.

## What the deployment does

In plain language, deployment first checks that the repository is complete, locks the deployment so two installs cannot run at the same time, then creates a backup before changing production. If PostgreSQL already exists, it creates a custom-format dump and restores that dump into an isolated temporary database to prove that the backup can actually be read.

The application is prepared in a new release directory rather than overwriting the running code. A candidate API starts on a temporary localhost-only port. The script tests the candidate, applies additive database changes, prepares the frontend, validates Nginx and HTTPS, then switches the production symlinks. If a blocking step fails after mutation begins, the rollback restores the previous application, service definitions, Nginx configuration and database state covered by the backup.

Existing pipeline files are preserved by default. Use `--pipeline replace` only when you deliberately want the repository pipeline to replace the installed one.

## Independent verification

After deployment, run:

```bash
./checking.sh
```

It asks for the VPS host, SSH user and domain, then runs the installed server-side checker. It does not deploy or modify the application.

The checker verifies, among other things:

- API health and localhost-only binding;
- PostgreSQL and the current market import;
- canonical `/app?view=...` routing;
- HTTPS and Nginx configuration;
- PWA version/update assets;
- authentication on private API surfaces;
- targeted weekly-email campaign database path;
- Predictive Radar tables and latest completed run;
- pipeline, alert, notification, feed and weekly-email timers;
- new-market-data push watcher;
- presence of the canonical Excel workbook.

A deployment is not considered healthy until `checking.sh` finishes with `PASS`.

## Useful paths on the VPS

```text
/opt/bvmac/current              current backend release
/var/www/bvmac-current          current frontend release
/opt/bvmac/pipeline             production data pipeline
/var/lib/bvmac                  persistent data and generated workbook
/etc/bvmac                      secrets and project configuration
/var/backups/bvmac              deployment backups
/etc/systemd/system/bvmac-*     BVMAC Market services and timers
```

## Operations and recovery

Previous releases and deployment backups are intentionally retained. Do not delete them during a deployment. Before changing service files or the database manually, understand what the deployment rollback already protects.

For troubleshooting, start with `./checking.sh`, then inspect the relevant service:

```bash
sudo systemctl status bvmac-api.service --no-pager
sudo journalctl -u bvmac-api.service -n 100 --no-pager
```

See the troubleshooting guide for common symptoms and diagnostic commands.
