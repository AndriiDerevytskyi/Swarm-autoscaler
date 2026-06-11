# Docker Swarm Autoscaler

Automatically scales Docker Swarm services based on CPU and RAM usage.
Two modes: **manager** (decision-making, web UI) and **agent** (per-node metrics collection).

---

## Quick Start

```bash
docker build -t swarm-autoscaler .
docker stack deploy -c stack.yml autoscaler
```

---

## Example Stack: Manager + Agent

```yaml
# stack.yml
services:
  autoscaler:
    image: swarm-autoscaler
    environment:
      AUTOSCALER_LOG_LEVEL: "INFO"
      AUTOSCALER_POLL_INTERVAL: "15"
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - autoscaler_data:/app/data
    networks:
      - autoscaler
    deploy:
      replicas: 1
      placement:
        constraints: [node.role == manager]
      resources:
        limits:   { memory: 128M, cpus: 0.5 }
        reservations: { memory: 64M, cpus: 0.1 }
      restart_policy:
        condition: on-failure

  autoscaler-agent:
    image: swarm-autoscaler
    environment:
      AUTOSCALER_ROLE: "agent"
      AUTOSCALER_MANAGER_URL: "http://autoscaler:8080"
      AUTOSCALER_LOG_LEVEL: "INFO"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - autoscaler
    deploy:
      mode: global
      resources:
        limits:   { memory: 64M, cpus: 0.2 }
        reservations: { memory: 32M, cpus: 0.05 }
      restart_policy:
        condition: on-failure

networks:
  autoscaler:
    driver: overlay
    attachable: true
    internal: true

volumes:
  autoscaler_data:
```

### How It Works

Every `POLL_INTERVAL` seconds, the manager:
1. Discovers services labeled `swarm.autoscaler.enable=true`
2. Aggregates its own metrics + agent reports from all nodes
3. If average CPU/RAM exceeds threshold → scale up (+1 replica, up to `max_replicas`)
4. If within normal range and cooldown expired → scale down (-1 replica, down to `min_replicas`)

Agents (`mode: global`, one per node) only collect `docker stats` and send to the manager.
No web UI, no database.

---

## Labels for Managed Services

```yaml
deploy:
  labels:
    - "swarm.autoscaler.enable=true"        # required
    - "swarm.autoscaler.min_replicas=1"     # default: 1
    - "swarm.autoscaler.max_replicas=10"    # default: 5
    - "swarm.autoscaler.cpu.threshold=70"   # default: 80 (percent)
    - "swarm.autoscaler.ram.threshold=80"   # default: 80 (percent)
    - "swarm.autoscaler.cooldown=5"         # default: 5 (minutes)
```

Labels must be under `deploy.labels`, not top-level `labels`.
The service must also have `deploy.resources.limits` set — CPU/RAM percentages are meaningless without them.

---

## Environment Variables

| Variable | Default | Role | Description |
|-----------|:---:|:---:|---------|
| `AUTOSCALER_ROLE` | `manager` | both | `manager` — full functionality, `agent` — metrics only |
| `AUTOSCALER_LOG_LEVEL` | `INFO` | both | `DEBUG` / `INFO` / `WARN` / `ERROR` |
| `AUTOSCALER_POLL_INTERVAL` | `15` | both | Poll interval, seconds |
| `AUTOSCALER_WEB_PORT` | `8080` | manager | Web UI port |
| `AUTOSCALER_MANAGER_URL` | `http://autoscaler:8080` | agent | Where to send metrics |
| `AUTOSCALER_NODE_NAME` | hostname | agent | Node identifier in reports |
| `AUTOSCALER_USER` | — | manager | Basic Auth login for web UI |
| `AUTOSCALER_HASH_PASSWORD` | — | manager | PBKDF2-SHA256 password hash |
| `AUTOSCALER_METRICS_USER` | — | manager | Login for `/api/metrics` |
| `AUTOSCALER_METRICS_HASH_PASSWORD` | — | manager | Password hash for `/api/metrics` |
| `AUTOSCALER_DEFAULT_MIN_REPLICAS` | `1` | manager | Default min |
| `AUTOSCALER_DEFAULT_MAX_REPLICAS` | `5` | manager | Default max |
| `AUTOSCALER_DEFAULT_CPU_THRESHOLD` | `80` | manager | Default CPU threshold, % |
| `AUTOSCALER_DEFAULT_RAM_THRESHOLD` | `80` | manager | Default RAM threshold, % |
| `AUTOSCALER_DEFAULT_COOLDOWN` | `5` | manager | Default cooldown, minutes |

---

## Generating a Password Hash

```bash
docker run --rm swarm-autoscaler \
  python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
```

Example auth configuration:

```yaml
environment:
  AUTOSCALER_USER: "admin"
  AUTOSCALER_HASH_PASSWORD: "pbkdf2:sha256:260000$..."
```

If both variables are unset, the web interface is unprotected.

---

## Web UI and API

After deployment: `http://<manager-ip>:8080`

| Page | Features |
|----------|-----------|
| Dashboard | Stats, alerts, sortable table, search, JSON export |
| Services | Service cards, sparklines, pause/resume with timeout, manual scale |
| Events | Full scale event history with per-service filter |
| About | Runtime parameters and label reference |

Dark/light theme. Real-time updates via SSE.

### Prometheus

```yaml
scrape_configs:
  - job_name: 'autoscaler'
    scrape_interval: 15s
    metrics_path: '/api/metrics'
    basic_auth:
      username: metrics
      password: your-password
    static_configs:
      - targets: ['manager-ip:8080']
```

Metrics: `autoscaler_replicas`, `autoscaler_cpu_pct`, `autoscaler_mem_pct`, `autoscaler_cpu_threshold`, `autoscaler_ram_threshold`, `autoscaler_paused`, `autoscaler_docker_ok`.

Ready-to-import Grafana dashboard: [`grafana-dashboard.json`](grafana-dashboard.json).

---

## REST API

| Method | Path | Description |
|-------|------|---------|
| `GET` | `/api/health` | Docker API status |
| `GET` | `/api/services` | Services with metrics |
| `GET` | `/api/events?limit=50&service=` | Event history |
| `DELETE` | `/api/events?service=` | Clear events |
| `POST` | `/api/services/<n>/scale` | Manual scale `{"replicas": N}` |
| `POST` | `/api/services/<n>/pause` | Pause `{"duration": 5}` (min) |
| `POST` | `/api/services/<n>/resume` | Resume |
| `GET` | `/api/metrics` | Prometheus |
| `GET` | `/api/stream` | SSE real-time |

### Examples

```bash
# Manual scale
curl -X POST http://localhost:8080/api/services/my-app/scale \
  -H "Content-Type: application/json" -d '{"replicas": 3}'

# Pause for 10 minutes
curl -X POST http://localhost:8080/api/services/my-app/pause \
  -H "Content-Type: application/json" -d '{"duration": 10}'
```

---

## Running Without Agents (Single Node)

If all managed services run on the manager node, agents are not needed — the manager
collects metrics directly:

```yaml
services:
  autoscaler:
    image: swarm-autoscaler
    ports: ["8080:8080"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - autoscaler_data:/app/data
    deploy:
      placement:
        constraints: [node.role == manager]

volumes:
  autoscaler_data:
```

## Limitations

- `docker stats` only sees containers on the local node — agents solve this by collecting metrics cluster-wide
- Scaling step: 1 replica per cycle
- The autoscaler never scales itself (services named `autoscaler*` are skipped)
