import os
import secrets
import signal
import threading
from datetime import datetime, timedelta
from typing import Dict, Set

import docker
from docker.errors import APIError, DockerException

from core.config import (
    LABEL_DEFAULTS,
    LOG_LEVEL,
    POLL_INTERVAL,
    WEB_PORT,
    parse_config,
)
from core.database import (
    expire_paused,
    get_node_metrics,
    get_replica_history,
    is_paused,
    meta_get,
    meta_set,
    record_event,
    record_replica_snapshot,
    run_migrations,
)
from core.logging import log
from core.stats import collect_stats, current_replicas
from web import server as web


def _banner() -> None:
    log.info(
        "╔══════════════════════════════════════════════════╗\n"
        "║           Docker Swarm Autoscaler                ║\n"
        "║  Monitors services and adjusts replica count     ║\n"
        "║  based on CPU / RAM usage against set thresholds ║\n"
        "╚══════════════════════════════════════════════════╝\n"
        "  Runtime configuration:\n"
        f"    AUTOSCALER_LOG_LEVEL     = {LOG_LEVEL}\n"
        f"    AUTOSCALER_POLL_INTERVAL = {POLL_INTERVAL}s\n"
        f"    AUTOSCALER_WEB_PORT      = {WEB_PORT}\n"
        f"    AUTOSCALER_AGENT_SECRET   = {'*' * 8} (persisted in DB)\n"
        "  Service label defaults (override per service):\n"
        f"    swarm.autoscaler.min_replicas  = {LABEL_DEFAULTS['swarm.autoscaler.min_replicas']}\n"
        f"    swarm.autoscaler.max_replicas  = {LABEL_DEFAULTS['swarm.autoscaler.max_replicas']}\n"
        f"    swarm.autoscaler.cpu.threshold = {LABEL_DEFAULTS['swarm.autoscaler.cpu.threshold']}%\n"
        f"    swarm.autoscaler.ram.threshold = {LABEL_DEFAULTS['swarm.autoscaler.ram.threshold']}%\n"
        f"    swarm.autoscaler.cooldown      = {LABEL_DEFAULTS['swarm.autoscaler.cooldown']} min"
    )


def _check_socket() -> None:
    sock = "/var/run/docker.sock"
    if not os.path.exists(sock):
        log.error("Docker socket not found at %s – mount it as a volume", sock)
        raise SystemExit(1)
    if not os.access(sock, os.R_OK | os.W_OK):
        log.error(
            "Docker socket %s is read-only – autoscaler needs rw access to scale services",
            sock,
        )
        raise SystemExit(1)
    log.info("  Docker socket %s  [OK, rw]", sock)


def _gather_stats(client, service_name: str) -> tuple:
    cpu_samples = []
    mem_samples = []

    local = collect_stats(client, service_name)
    if local:
        cpu_samples.append(local["cpu_pct"])
        mem_samples.append(local["mem_pct"])

    for row in get_node_metrics(service_name):
        cpu_samples.append(row["cpu_pct"])
        mem_samples.append(row["mem_pct"])

    if not cpu_samples:
        return None, None

    return (
        min(100.0, sum(cpu_samples) / len(cpu_samples)),
        min(100.0, sum(mem_samples) / len(mem_samples)),
    )


def main() -> None:
    _banner()

    _check_socket()

    run_migrations()

    agent_secret = meta_get("agent_secret")
    if not agent_secret:
        agent_secret = secrets.token_hex(32)
        meta_set("agent_secret", agent_secret)

    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        log.error("Cannot connect to Docker daemon: %s", exc)
        raise SystemExit(1)

    web.setup(LOG_LEVEL, POLL_INTERVAL, WEB_PORT, LABEL_DEFAULTS,
              agent_secret=agent_secret)
    t = threading.Thread(target=web.start, kwargs={"port": WEB_PORT}, daemon=True)
    t.start()
    log.info("  Web UI started at http://0.0.0.0:%d", WEB_PORT)

    _shutdown = threading.Event()

    def _handle_signal(signum, frame):
        log.info("Received signal %d – shutting down gracefully", signum)
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    cooldown:    Dict[str, datetime] = {}
    last_action: Dict[str, str]      = {}
    last_scale:  Dict[str, datetime] = {}
    warned:      Set[tuple]          = set()
    warned_limits: Set[str]          = set()

    while not _shutdown.is_set():
        try:
            services = client.services.list()
            web.set_docker_health(True)
        except APIError as exc:
            log.error("Failed to list Swarm services: %s", exc)
            web.set_docker_health(False)
            _shutdown.wait(timeout=POLL_INTERVAL)
            continue

        managed: Set[str] = set()

        # auto-resume paused services whose timer expired
        for svc_name in expire_paused():
            log.info("%s: pause expired – resuming autoscaling", svc_name)

        for svc in services:
            try:
                svc.reload()
                labels: dict = svc.attrs.get("Spec", {}).get("Labels", {}) or {}
                cfg = parse_config(svc.name, labels, warned)
                if cfg is None:
                    continue

                name     = svc.name
                replicas = current_replicas(svc)

                # never scale the autoscaler itself
                if name.startswith("autoscaler"):
                    log.debug("%s: skipping (autoscaler self-protection)", name)
                    continue

                # require at least one resource limit — otherwise thresholds are meaningless
                task_tmpl = svc.attrs.get("Spec", {}).get("TaskTemplate", {})
                res_limits = task_tmpl.get("Resources", {}).get("Limits") or {}
                has_cpu = bool(res_limits.get("NanoCPUs") or res_limits.get("CPUs"))
                has_mem = bool(res_limits.get("MemoryBytes"))
                if not has_cpu and not has_mem:
                    if name not in warned_limits:
                        log.warning(
                            "%s: labeled for autoscaling but has no resource limits set "
                            "(deploy.resources.limits) — skipping. CPU/RAM percentages "
                            "are meaningless without limits.", name
                        )
                        warned_limits.add(name)
                    continue

                managed.add(name)

                record_replica_snapshot(name, replicas)

                paused   = is_paused(name)
                alerts   = []
                cpu      = 0.0
                mem      = 0.0

                if paused:
                    alerts.append("Autoscaling paused")
                    log.debug("%s: autoscaling is paused – skipping", name)
                else:
                    cpu, mem = _gather_stats(client, name)

                    if cpu is None:
                        log.debug("%s: no containers visible on any node (replicas=%d) – skipping",
                                  name, replicas)
                        alerts.append("No containers visible on any node")
                    else:
                        log.debug(
                            "%s  replicas=%d  avg cpu=%.1f%%  avg mem=%.1f%%  "
                            "(thresholds cpu>%.0f%%  mem>%.0f%%  cooldown=%dmin)",
                            name, replicas, cpu, mem,
                            cfg["cpu_threshold"], cfg["ram_threshold"], cfg["cooldown_minutes"],
                        )

                        overloaded = cpu >= cfg["cpu_threshold"] or mem >= cfg["ram_threshold"]

                        if overloaded:
                            if replicas < cfg["max_replicas"]:
                                new = replicas + 1
                                reason = f"cpu={cpu:.1f}% mem={mem:.1f}%"
                                log.info("%s: SCALE UP  %d -> %d  (%s)",
                                         name, replicas, new, reason)
                                svc.scale(new)
                                record_event(name, "up", replicas, new, reason, cpu, mem)
                                replicas = new
                                last_action[name] = "up"
                                last_scale[name]  = datetime.now()
                                cooldown[name]    = datetime.now() + timedelta(minutes=cfg["cooldown_minutes"])
                            else:
                                alerts.append(f"Overloaded at max ({cfg['max_replicas']} replicas)")
                                log.warning(
                                    "%s: overloaded (cpu=%.1f%%  mem=%.1f%%) "
                                    "but already at max_replicas=%d – cannot scale up",
                                    name, cpu, mem, cfg["max_replicas"],
                                )
                        else:
                            gate = cooldown.get(name, datetime.min)
                            if datetime.now() >= gate and replicas > cfg["min_replicas"]:
                                new = replicas - 1
                                reason = f"cpu={cpu:.1f}% mem={mem:.1f}% cooldown passed"
                                log.info("%s: SCALE DOWN  %d -> %d  (%s)",
                                         name, replicas, new, reason)
                                svc.scale(new)
                                record_event(name, "down", replicas, new, reason, cpu, mem)
                                replicas = new
                                last_action[name] = "down"
                                last_scale[name]  = datetime.now()
                                cooldown[name]    = datetime.now() + timedelta(minutes=cfg["cooldown_minutes"])

                history = get_replica_history(name, 60)

                web.update_service(name, {
                    "name":             name,
                    "replicas":         replicas,
                    "min_replicas":     cfg["min_replicas"],
                    "max_replicas":     cfg["max_replicas"],
                    "cpu_threshold":    cfg["cpu_threshold"],
                    "ram_threshold":    cfg["ram_threshold"],
                    "cooldown_minutes": cfg["cooldown_minutes"],
                    "cpu_pct":          cpu,
                    "mem_pct":          mem,
                    "last_action":      last_action.get(name),
                    "last_scale_at":    last_scale.get(name),
                    "cooldown_until":   cooldown.get(name),
                    "paused":           paused,
                    "alerts":           alerts,
                    "history":          history,
                })

            except Exception as exc:
                log.error("Unexpected error processing service %s: %s",
                          svc.name, exc, exc_info=True)

        for gone in web.managed_names() - managed:
            web.remove_service(gone)

        web.broadcast_sse()

        _shutdown.wait(timeout=POLL_INTERVAL)

    log.info("Shutdown complete")
