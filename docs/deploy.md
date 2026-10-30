# Deploying TriageLens

TriageLens runs on the shared EC2 host, `portfolio-apps` (t3.small: 2 vCPU, 2 GB RAM plus 2 GB swap,
20 GB gp3, ap-southeast-2, Elastic IP 3.25.14.19), alongside SplitSync. It will be live at
**https://3-25-14-19.sslip.io/triagelens/**: sslip.io resolves the name to the IP, and Caddy gets
a free Let's Encrypt certificate for it with HTTPS and HTTP/2.

| Compose project | Services | Notes |
|---|---|---|
| `portfolio` (`/opt/portfolio`, from `splitsync/deploy/portfolio/`) | caddy, postgres (pgvector 0.8.7-pg16), redis 8 | shared; published ports 80/443 |
| `triagelens` (`deploy/compose.prod.yaml`) | triagelens-app (500 MB limit), triagelens-migrate (one-off), triagelens-seed (one-off) | on the `portfolio` network |

The instance: Ubuntu 24.04, IMDSv2 required, encrypted EBS, CPU credits in standard mode (no
surplus-credit charges), security group `portfolio-apps` with 80/443 open and 22 open for key-only
SSH. Docker is already installed and a 2 GB swap file is active.

## One-time setup on the host

```bash
sudo mkdir -p /opt/triagelens/src && sudo chown -R ubuntu: /opt/triagelens
git clone https://github.com/Srinu-design/triagelens /opt/triagelens/src

# 1. Role, database and pgvector extension in shared PostgreSQL (superuser 'portfolio'):
pw=$(openssl rand -hex 24)
docker exec -i portfolio-postgres-1 psql -U portfolio -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE triagelens LOGIN PASSWORD '$pw';" \
  -c "CREATE DATABASE triagelens OWNER triagelens;"
docker exec -i portfolio-postgres-1 psql -U portfolio -d triagelens -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 2. Redis DB index: SplitSync uses DB 0 (redis://redis:6379/0); TriageLens uses DB 1.

# 3. Create /opt/triagelens/.env with mode 600:
cat > /opt/triagelens/.env <<EOF
DATABASE_URL=postgresql+asyncpg://triagelens:$pw@postgres:5432/triagelens
REDIS_URL=redis://redis:6379/1
APP_ENV=prod
EMBED_THREADS=1
# LLM settings are optional and off by default (leave unset to keep LLM path disabled):
# LLM_BASE_URL=
# LLM_API_KEY=
# LLM_MODEL=
# LLM_JSON_MODE=false
# LLM_DAILY_BUDGET=200
EOF
chmod 600 /opt/triagelens/.env
```

## Caddy routing

Caddy in `/opt/portfolio/Caddyfile` (managed in the splitsync repository) routes traffic to TriageLens.
The manager adds these directives inside the `{$SITE_ADDRESS}` block in `deploy/portfolio/Caddyfile`:

```caddy
	redir /triagelens /triagelens/
	handle_path /triagelens/* {
		reverse_proxy triagelens-app:8000
	}
```

Then reload Caddy without downtime:
```bash
docker exec portfolio-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

## A release

`/opt/triagelens/src/deploy/deploy.sh <git ref>` (exit codes and steps in the script's header):

1. build `triagelens:<sha>` from `/opt/triagelens/src` at that ref;
2. run migrations with `triagelens-migrate` (`alembic upgrade head`);
3. seed the database with `triagelens-seed` (`python -m app.seed_load --if-empty`, idempotent);
4. replace `triagelens-app` and wait until healthy (`/readyz`, allowing model warm-up);
5. **smoke test**, `python -m app.smoke` in the new container: checks `/readyz`, hybrid search for
   `BUG-102`, preview duplicate verdict (`FEAT-201`), and CSP headers.

If 4 or 5 fail, the previous release is restored automatically and smoke tested too.
`deploy.sh --rollback` and `deploy.sh --status` do what they say. Every step is logged to
`/opt/triagelens/deploy-history.log`.

## Deploying from CI

`.github/workflows/deploy.yml` deploys every push to main once `ci` is green, and on demand any
sha or `--rollback` (Actions > deploy > Run workflow). It connects via SSH with a restricted
forced-command key in `~/.ssh/authorized_keys`:

```
restrict,command="/opt/triagelens/src/deploy/deploy.sh --from-ssh" ssh-ed25519 ... triagelens-github-deploy
```

Whatever the key sends arrives as `SSH_ORIGINAL_COMMAND`, and `deploy.sh --from-ssh` accepts
only a full commit sha, `--rollback` or `--status`.

One-time key generation and GitHub setup:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/triagelens-deploy -N "" -C triagelens-github-deploy
gh secret set EC2_SSH_KEY -R Srinu-design/triagelens < ~/.ssh/triagelens-deploy
ssh-keyscan -t ed25519 3.25.14.19 | gh secret set EC2_KNOWN_HOSTS -R Srinu-design/triagelens
gh variable set EC2_HOST -R Srinu-design/triagelens -b 3.25.14.19
gh variable set PUBLIC_URL -R Srinu-design/triagelens -b https://3-25-14-19.sslip.io/triagelens/
```

Append the public key `~/.ssh/triagelens-deploy.pub` with the forced-command prefix to
`/home/ubuntu/.ssh/authorized_keys` on `portfolio-apps`.

## Shared-host memory check (§15)

With both SplitSync and TriageLens running on the 2 GB host, verify memory and swap behavior.
Turn the LLM path off (unset `LLM_*`) to benchmark the host, not an API provider.

1. Record baseline idle stats:
```bash
docker stats --no-stream
free -m
```

2. Run three bursts of 10 concurrent triage previews, spaced 1 minute apart:
```bash
for burst in 1 2 3; do
  echo "Burst $burst at $(date -u +%T)"
  for i in $(seq 1 10); do
    curl -s -X POST http://127.0.0.1:8000/api/triage/preview \
      -H "Content-Type: application/json" \
      -d '{"title":"Night mode for checkout on Android","description":"Would love a dark theme when paying in the Android app.","category_override":"FEATURE_REQUEST"}' > /dev/null &
  done
  wait
  docker stats --no-stream
  free -m
  sleep 60
done
```

3. Record the peak memory per service and swap usage. The measured results go under the
"EC2 memory" heading in `bench/RESULTS.md`.

## Rollback

To roll back manually:
- Via GitHub Actions: go to **Actions > deploy > Run workflow**, type `--rollback`, and click Run.
- Or directly on the host:
```bash
/opt/triagelens/src/deploy/deploy.sh --rollback
```
To check current and previous releases:
```bash
/opt/triagelens/src/deploy/deploy.sh --status
```
