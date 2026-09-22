# Deploying the tracker

The whole stack runs from one `docker compose up` on a single server. That is
deliberate: the import pipeline, the API, the public site and the monthly CPRA
sync are all modest workloads, and one box you can reason about beats four
managed services you cannot.

This guide uses a DigitalOcean droplet because that is the likely first home,
but nothing here is specific to DigitalOcean — any Ubuntu host works, as does
Hetzner, Linode or an EC2 instance.

## What it needs and what it costs

| Component | Requirement | Why |
| --- | --- | --- |
| CPU | 2 vCPU to start | OCR is the only heavy work. A scanned 16-page permit takes about 50 seconds on one core. |
| RAM | 4 GB to start | Postgres, Redis, the API, the Next.js server and a tesseract process. |
| Disk | 25 GB to start | The database is small. Source documents are the variable — see below. |
| Network | Unrestricted outbound | The tracker calls county GIS, CAL FIRE, the Census geocoder and Inquisitor. |

**A 4 GB / 2 vCPU droplet (about $24/month) runs Lassen plus several
counties comfortably.** Move to 8 GB / 4 vCPU if you are importing large
scanned batches regularly, since OCR is what saturates the box.

### About disk

Scanned permits are five to six megabytes each, so a statewide archive over
several years reaches tens of gigabytes. Two things keep that off this server:

* Where documents arrive through Inquisitor, the original stays in
  Inquisitor's evidence vault and the tracker keeps only the extracted text —
  roughly 25 KB for a 6 MB permit. See `docs/ROADMAP.md`.
* Where documents are uploaded directly, set `TRACKER_STORAGE_BACKEND=s3` and
  point it at DigitalOcean Spaces (S3-compatible, about $5/month for 250 GB).
  The compose file keeps them on a local volume by default, which is fine
  while the archive is small.

## The quick way

On a fresh Ubuntu 24.04 droplet, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/menkmt/herbicide-tracker/claude/intelligent-babbage-ysc4wf/deploy/bootstrap-droplet.sh | bash
```

That installs Docker, sets the firewall, clones the repo, generates secrets,
builds and starts everything, runs the migrations, and probes the GIS services
so their field names can be wired up. It deliberately stops short of exposing
anything to the internet — the reverse proxy step below is separate and
deliberate. It is safe to re-run.

The rest of this document is what that script does, step by step, for when you
want to do it by hand or understand what happened.

## First deployment

### 1. Create the droplet

Ubuntu 24.04, 4 GB / 2 vCPU, in a region near you. Add your SSH key during
creation rather than using a root password.

### 2. Install Docker

```bash
ssh root@YOUR_DROPLET_IP
apt-get update && apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### 3. Lock the box down before anything is listening

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80,443/tcp
ufw enable
```

Ports 8000 and 3000 are deliberately **not** opened. The reverse proxy in
step 6 is the only thing the internet talks to.

### 4. Get the code and configure it

```bash
git clone https://github.com/menkmt/herbicide-tracker.git /opt/tracker
cd /opt/tracker
cp .env.example .env
```

Edit `.env`. The settings that must be changed before this is reachable:

```bash
TRACKER_ENVIRONMENT=production
TRACKER_SECRET_KEY=$(openssl rand -hex 32)
TRACKER_ADMIN_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
TRACKER_PUBLIC_BASE_URL=https://tracker.example.org
```

The application refuses to start in production with the development defaults
still in place, so a missed value fails loudly rather than shipping a site
with a guessable admin token.

### 5. Start it

```bash
docker compose up -d
docker compose exec api alembic upgrade head
docker compose ps        # every service should be healthy
```

### 6. Put a reverse proxy in front

Caddy is the least work because it obtains and renews certificates on its own.

```bash
apt-get install -y caddy
```

`/etc/caddy/Caddyfile`:

```
tracker.example.org {
    # The public site.
    handle {
        reverse_proxy 127.0.0.1:3000
    }

    # The API, for the WordPress plugin and any subscriber.
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }

    # The admin dashboard. Restrict it by source address as well as by token:
    # the token is the real control, but there is no reason for the whole
    # internet to be able to reach an upload form.
    handle /admin* {
        @allowed remote_ip YOUR.OFFICE.IP.ADDRESS
        handle @allowed {
            reverse_proxy 127.0.0.1:8000
        }
        respond 404
    }
}
```

```bash
systemctl reload caddy
```

Point your DNS A record at the droplet first, or Caddy cannot complete the
certificate challenge.

### 7. Check it works

```bash
curl -s https://tracker.example.org/api/meta | head
```

Then open `https://tracker.example.org/admin`, paste the admin token, and drop
in a county's documents.

## Wiring up the GIS services

This is the step that unblocks parcel maps and radius search, and it is the
main reason to have a server with real network access.

```bash
docker compose exec api python -m scripts.probe_gis --county lassen --search-parcels
```

It prints each service's layers, and for a parcel layer it prints the field
names and a suggested `ArcGisFieldMap`. Put the confirmed values into
`backend/app/providers/parcels/registry.py` and add the county slug to
`VERIFIED_COUNTIES` — until a county is in that list the tracker returns no
parcels rather than possibly-wrong ones.

Check `docs/LICENSING.md` before enabling a county: parcel data terms vary,
and that is the one genuine licensing risk in the stack.

## Running more than one API worker

The rate limiter in `app/api/deps.py` keeps its counters in Redis when
`TRACKER_REDIS_URL` is set, so several workers share one budget. With Redis
unreachable it falls back to per-process counting and logs that it has done
so — which means the effective limit becomes the configured limit times the
number of workers. That is a degradation, not a failure, but it is worth
knowing about if you are selling a rate-limited API tier.

To run multiple workers, override the API command:

```yaml
  api:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## Backups

The database holds everything derived; the source documents hold the evidence.

```bash
# Nightly database dump, kept for 14 days.
cat > /etc/cron.daily/tracker-backup <<'EOF'
#!/bin/sh
cd /opt/tracker
docker compose exec -T db pg_dump -U tracker tracker | gzip > \
  /var/backups/tracker-$(date +%F).sql.gz
find /var/backups -name 'tracker-*.sql.gz' -mtime +14 -delete
EOF
chmod +x /etc/cron.daily/tracker-backup
```

Enable DigitalOcean's droplet backups as well, and if you are keeping source
documents locally rather than in Inquisitor, make sure the `source-files`
volume is included in whatever you are backing up.

## The monthly CPRA sync

The `cpra-sync` service wakes daily, checks whether it is the configured day of
the month, and runs the refresh if so. It does nothing until Inquisitor is
configured:

```bash
TRACKER_INQUISITOR_ENABLED=true
TRACKER_INQUISITOR_BASE_URL=http://inquisitor.internal:8080
TRACKER_INQUISITOR_WORKSPACE_ID=...
TRACKER_INQUISITOR_ACTOR=...
TRACKER_INQUISITOR_AUTO_SEND=false
```

Leave `AUTO_SEND` false unless you have decided that records requests should go
out to agencies without a person approving them. Inquisitor authenticates by
trusted-proxy header, so its address must not be publicly reachable, and the
tracker's address must be in Inquisitor's `trusted_proxy_addresses`.

Run it by hand first:

```bash
docker compose run --rm cpra-sync python -m scripts.run_cpra_sync --dry-run
```

## Connecting WordPress

The plugin calls the API server-side, so WordPress needs to reach it but
visitors never do.

1. Copy `wordpress-plugin/ground-truth-tracker/` into `wp-content/plugins/`.
2. Activate it, then set the API URL under Settings → Herbicide Tracker.
3. Re-save Settings → Permalinks so the rewrite rules take effect.

## Updating

```bash
cd /opt/tracker
git pull
docker compose build
docker compose up -d
docker compose exec api alembic upgrade head
```

Migrations are additive and have been round-tripped, but take a database dump
before upgrading anyway.

## When one box is not enough

The first things to move off, in order:

1. **Postgres** → DigitalOcean Managed Databases, which supports PostGIS.
   Removes the most operationally demanding component.
2. **Source documents** → Spaces, via `TRACKER_STORAGE_BACKEND=s3`.
3. **OCR** → a separate worker droplet, since it is the only workload that
   saturates CPU and it is entirely asynchronous.

The API and the public site scale horizontally behind a load balancer without
changes, provided Redis is reachable so rate limiting stays shared.
