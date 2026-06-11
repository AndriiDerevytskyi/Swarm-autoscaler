# Docker Swarm Autoscaler

Automatically scales Docker Swarm services based on real CPU and RAM usage.
Runs as a container inside the stack, communicates with the cluster via Docker socket.
Features a web interface for monitoring, manual replica control, and autoscaling pause.
Exposes Prometheus metrics and includes a ready-to-use Grafana dashboard.

---

## How It Works

Every `AUTOSCALER_POLL_INTERVAL` seconds:

1. **Manager** discovers all services labeled `swarm.autoscaler.enable=true`
2. **Agent** (one per node) collects `docker stats` and sends them to the manager
3. Manager aggregates its own metrics + agent reports → average across all nodes
4. If average usage exceeds threshold → scale up by 1 replica (up to `max_replicas`)
5. If usage is normal and cooldown has expired → scale down by 1 replica (down to `min_replicas`)
6. After each scaling event, a cooldown timer starts and the event is recorded in SQLite
7. Updates are streamed to clients in real time via SSE

---

## Project Structure

```
swarm-autoscaler/
├── main.py                   # entry point
├── Dockerfile                # image build (with HEALTHCHECK)
├── DOCKER_HUB.md             # Docker Hub overview
├── .dockerignore
├── .github/workflows/build.yml  # CI: build + publish to Docker Hub
├── requirements.txt          # flask, docker
├── docker-compose.yml        # Swarm stack deployment
├── prometheus.yml.example    # sample Prometheus scrape config
├── grafana-dashboard.json    # ready-to-import Grafana dashboard
├── healthcheck.py            # universal healthcheck (manager + agent)
├── data/
│   └── autoscaler.db         # SQLite (auto-created on first run)
├── core/
│   ├── __init__.py
│   ├── logging.py            # custom formatter, logger setup
│   ├── config.py             # env vars, defaults, parse_config()
│   ├── database.py           # SQLite connection, migrator, events/history/pause/meta
│   ├── stats.py              # CPU/RAM metrics via Docker API
│   ├── engine.py             # main loop (manager)
│   ├── agent.py              # lightweight metrics collector (agent)
│   └── migrations/
│       ├── __init__.py
│       ├── 001_initial.sql   # tables: events, replica_history, paused_services
│       ├── 002_pause_timeout.sql  # resume_after column
│       ├── 003_node_metrics.sql   # agent reports table
│       ├── 004_meta.sql           # key-value store (secrets)
│       └── 005_auth.sql           # users table for web UI auth
└── web/
    ├── server.py             # Flask REST API + SSE
    ├── static/
    │   ├── style.css         # dark/light theme
    │   └── app.js            # SPA (zero external dependencies)
    └── templates/
        └── index.html        # SPA shell
```

---

## Requirements

- Docker Engine in **Swarm** mode (`docker swarm init`)
- Autoscaler must run on a **manager node**
- Python 3.10+ (for local development only; everything is included in the container)

---

## Operating Modes

The autoscaler supports two modes to solve the container visibility problem in multi-node Swarm:
`docker stats` via socket only sees containers on the local node.

### Manager (default)

One instance on the manager node. Collects metrics from its own node, makes scaling
decisions, serves the web UI, SSE, Prometheus metrics, and SQLite database.

```
┌─ Manager Node ──────────────────────────────────┐
│  autoscaler (AUTOSCALER_ROLE=manager)           │
│  ├── own docker stats                           │
│  ├── ← aggregates agent reports                 │
│  ├── makes scaling decisions                    │
│  ├── Web UI :8080                               │
│  ├── /api/metrics (Prometheus)                  │
│  └── SQLite                                     │
└─────────────────────────────────────────────────┘
```

### Agent (global)

One instance per node (`mode: global`). Only collects `docker stats`
and sends reports to the manager. No web UI, no database, no decision-making.
Minimal resource footprint (64 MB RAM, 0.2 CPU).

```
┌─ Worker 1 ─────┐ ┌─ Worker 2 ─────┐ ┌─ Worker 3 ─────┐
│ agent          │ │ agent          │ │ agent          │
│ docker stats → │ │ docker stats → │ │ docker stats → │
└────┬───────────┘ └────┬───────────┘ └────┬───────────┘
     │    POST /api/agent/report            │
     └──────────────┬───────────────────────┘
                    ▼
            ┌─ Manager ───┐
            │ autoscaler  │
            └─────────────┘
```

### Configuration

| Variable | Manager | Agent | Description |
|-----------|:---:|:---:|---------|
| `AUTOSCALER_ROLE` | `manager` | `agent` | Operating mode |
| `AUTOSCALER_MANAGER_URL` | — | `http://autoscaler:8080` | Manager URL for sending metrics |

The agent can be deployed globally via docker-compose (`mode: global`) or manually
on each node as a standalone container.

### docker-compose.yml

```yaml
services:
  autoscaler:
    image: ozzzyad/swarm-autoscaler:latest
    deploy:
      placement: [node.role == manager]

  agent:
    image: ozzzyad/swarm-autoscaler:latest
    environment:
      AUTOSCALER_ROLE: "agent"
      AUTOSCALER_MANAGER_URL: "http://autoscaler:8080"
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    deploy:
      mode: global
```

---

## Configuring Managed Services

To bring a service under autoscaler management, add labels under `deploy.labels`:

```yaml
services:
  my-app:
    image: my-app:latest
    deploy:
      mode: replicated
      replicas: 1
      labels:
        - "swarm.autoscaler.enable=true"
        - "swarm.autoscaler.min_replicas=1"
        - "swarm.autoscaler.max_replicas=10"
        - "swarm.autoscaler.cpu.threshold=70"
        - "swarm.autoscaler.ram.threshold=80"
        - "swarm.autoscaler.cooldown=5"
```

### Label Reference

| Label | Required | Default | Description |
|-------|:---:|:---:|---------|
| `swarm.autoscaler.enable` | ✅ | — | Enables autoscaler management |
| `swarm.autoscaler.min_replicas` | ❌ | `1` | Minimum replicas |
| `swarm.autoscaler.max_replicas` | ❌ | `5` | Maximum replicas |
| `swarm.autoscaler.cpu.threshold` | ❌ | `80` | CPU threshold, % |
| `swarm.autoscaler.ram.threshold` | ❌ | `80` | RAM threshold, % |
| `swarm.autoscaler.cooldown` | ❌ | `5` | Cooldown, minutes |

> **Important:** labels must be under `deploy.labels`, not top-level `labels`.
> The autoscaler skips services named `autoscaler*` — it never tries to scale itself.
> **Resource limits are required:** if a service has the autoscaler label but no
> `deploy.resources.limits` (CPU or memory), it is skipped with a warning — CPU/RAM
> percentages are meaningless without limits.

---

## Environment Variables

| Variable | Default | Description |
|-----------|:---:|---------|
| `AUTOSCALER_LOG_LEVEL` | `INFO` | Log level: `DEBUG` / `INFO` / `WARN` / `ERROR` |
| `AUTOSCALER_POLL_INTERVAL` | `15` | Service poll interval, seconds |
| `AUTOSCALER_WEB_PORT` | `8080` | Web UI port |
| `AUTOSCALER_DEFAULT_MIN_REPLICAS` | `1` | Default min replicas |
| `AUTOSCALER_DEFAULT_MAX_REPLICAS` | `5` | Default max replicas |
| `AUTOSCALER_DEFAULT_CPU_THRESHOLD` | `80` | Default CPU threshold, % |
| `AUTOSCALER_DEFAULT_RAM_THRESHOLD` | `80` | Default RAM threshold, % |
| `AUTOSCALER_DEFAULT_COOLDOWN` | `5` | Default cooldown, minutes |

---

## Authentication

### Web UI

Initially the web UI is **unprotected**. To set up authentication, go to the **About** page
and fill in the "Security — Setup Authentication" form with a username and password.
The password hash is stored in SQLite. To change the password later, use the
"Change Password" form on the same page.

### Prometheus Metrics

By default `/api/metrics` is open. Go to **About → Prometheus Metrics** and click
"Enable Auth & Generate Password". The generated password is **shown once** — save it
for your Prometheus config.

You can regenerate the password or disable auth at any time from the same section.

Example Prometheus scrape config with auth:

```yaml
scrape_configs:
  - job_name: 'autoscaler'
    scrape_interval: 15s
    metrics_path: '/api/metrics'
    basic_auth:
      username: prometheus
      password: <generated-password>
    static_configs:
      - targets: ['swarm-manager:8080']
```

---

## Build and Deploy

```bash
# 1. Download the stack file
curl -O https://raw.githubusercontent.com/AndriiDerevytskyi/Swarm-autoscaler/refs/heads/main/docker-compose.yml

# 2. Deploy the stack
docker stack deploy -c docker-compose.yml autoscaler

# 3. Verify
docker stack services autoscaler
docker service logs -f autoscaler_autoscaler
```

On successful startup you will see (logs are JSON):

```json
{"time": "2026-06-10T18:00:00Z", "level": "INFO", "message": "Docker socket /var/run/docker.sock  [OK, rw]"}
{"time": "2026-06-10T18:00:00Z", "level": "INFO", "message": "Web UI started at http://0.0.0.0:8080"}
{"time": "2026-06-10T18:00:15Z", "level": "INFO", "message": "my-app: SCALE UP  1 -> 2  (cpu=87.3% mem=45.1%)"}
```

---

## Web Interface

`http://<swarm-node-ip>:8080`

### Dashboard
Summary stats: service count, total replicas, services at max/in cooldown.
Alert panel for overloaded and paused services.
Sortable table with search/filter by name. JSON export.

### Services
Service cards: replica dots, replica history sparkline for the last hour,
CPU/RAM progress bars with threshold markers, cooldown timer.
**Pause** with timeout selector (5/10/15/30 min or indefinitely) / **Resume**.
Manual replica control with min/max validation.

### Events
Chronological log of all scaling events, filterable by service.
Data stored in SQLite, persists across restarts.
Clear events button (all or per-service).

### About
Runtime parameters, label reference, and security: set up web UI authentication,
enable/disable Prometheus metrics auth with auto-generated passwords.

> Zero external CDN dependencies, fully offline-capable. Data updates in real time
> via **Server-Sent Events (SSE)** — no periodic polling.
> Dark/light theme, preference saved in localStorage.

---

## REST API

| Method | Path | Description | Auth |
|-------|------|---------|:---:|
| `GET` | `/api/health` | Docker API connection status | — |
| `GET` | `/api/services` | List of managed services | Basic |
| `GET` | `/api/config` | Runtime configuration | Basic |
| `GET` | `/api/events?limit=50&service=` | Event history | Basic |
| `DELETE` | `/api/events?service=` | Clear events (all or per-service) | Basic |
| `POST` | `/api/services/<name>/scale` | Set replicas `{"replicas": N}` | Basic |
| `POST` | `/api/services/<name>/pause` | Pause `{"duration": 5}` (min, 0=forever) | Basic |
| `POST` | `/api/services/<name>/resume` | Resume autoscaling | Basic |
| `GET` | `/api/auth/status` | Check if web UI auth is configured | — |
| `POST` | `/api/auth/setup` | Set up credentials (first time only) | — |
| `POST` | `/api/auth/change` | Change password | Basic |
| `GET` | `/api/metrics/auth/status` | Metrics auth state | Basic |
| `POST` | `/api/metrics/auth/enable` | Enable + generate password | Basic |
| `POST` | `/api/metrics/auth/disable` | Disable metrics auth | Basic |
| `POST` | `/api/metrics/auth/regenerate` | Regenerate metrics password | Basic |
| `GET` | `/api/services/<name>/history?minutes=60` | Replica history for sparklines | Basic |
| `GET` | `/api/stream` | SSE real-time stream | — |
| `GET` | `/api/metrics` | Prometheus metrics | Metrics |
| `POST` | `/api/agent/report` | Receive agent metrics | Agent secret |
| `GET` | `/api/agent/secret` | Bootstrap: agent secret retrieval | Overlay IP |

Examples:

```bash
# Manual scale
curl -X POST http://localhost:8080/api/services/my-app/scale \
  -H "Content-Type: application/json" -d '{"replicas": 3}'

# Pause for 10 minutes
curl -X POST http://localhost:8080/api/services/my-app/pause \
  -H "Content-Type: application/json" -d '{"duration": 10}'

# Clear events for a single service
curl -X DELETE http://localhost:8080/api/events?service=my-app
```

---

## Prometheus and Grafana

### Prometheus

Copy `prometheus.yml.example` into your Prometheus config. Supported discovery methods:
- **static_configs** — single autoscaler by IP
- **dns_sd_configs** — Swarm with multiple replicas
- **file_sd_configs** — dynamic targets

Sample Prometheus scrape config (use the password generated in the About page):

```yaml
scrape_configs:
  - job_name: 'autoscaler'
    scrape_interval: 15s
    metrics_path: '/api/metrics'
    basic_auth:
      username: prometheus
      password: <generated-password>
    static_configs:
      - targets: ['swarm-manager:8080']
```

### Metrics

| Metric | Type | Labels |
|---------|-----|--------|
| `autoscaler_replicas` | gauge | `service`, `instance`, `job` |
| `autoscaler_cpu_pct` | gauge | `service` |
| `autoscaler_mem_pct` | gauge | `service` |
| `autoscaler_cpu_threshold` | gauge | `service` |
| `autoscaler_ram_threshold` | gauge | `service` |
| `autoscaler_paused` | gauge | `service` |
| `autoscaler_docker_ok` | gauge | — |

### Grafana

Import `grafana-dashboard.json` into Grafana. The dashboard includes:

- **Overview** — 5 stat panels: Docker API, services, replicas, paused, overloaded
- **CPU & Memory** — time series with thresholds, table legend
- **Replicas** — stepped time series per service
- **Current State** — table with all metrics, color-background cells
- **Overloaded Services** — table of only overloaded services

Variables: `$datasource`, `$instance` (multi-select), `$service` (multi-select) — zero hardcoding.

---

## Log Levels

All logs are JSON with fields `time`, `level`, `message`.

| Level | What is logged |
|---------|-----------|
| `ERROR` | Docker API errors, loop exceptions |
| `WARN` | Missing labels, service at max capacity, stats read errors |
| `INFO` | Startup, scale up/down events, pause expiration |
| `DEBUG` | Per-container stats on every tick, skipped services |

---

## Limitations

- **Container visibility**: `docker stats` only sees containers on the local node. The `agent` mode (global) solves this — one agent per node, manager aggregates metrics cluster-wide.
- **Scaling step**: exactly 1 replica per cycle. Under sudden load spikes, scaling happens in steps across cycles.
- **Cooldown**: after any scale event, a timer starts that blocks scale-down for the full duration.
- **Autoscaler never scales itself**: services named `autoscaler*` are skipped.
