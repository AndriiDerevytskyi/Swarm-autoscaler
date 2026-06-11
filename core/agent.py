import os
import signal
import socket
import threading
import time
import urllib.request
import urllib.error
import json

import docker
from docker.errors import APIError, DockerException

from core.config import POLL_INTERVAL, parse_config
from core.logging import log
from core.stats import collect_stats, current_replicas

MANAGER_URL = os.getenv("AUTOSCALER_MANAGER_URL", "http://autoscaler:8080")
NODE_NAME   = os.getenv("AUTOSCALER_NODE_NAME", socket.gethostname())

_AGENT_SECRET      = ""
_bootstrap_cooldown = 0.0


def _bootstrap() -> bool:
    global _AGENT_SECRET, _bootstrap_cooldown
    now = time.time()
    if now < _bootstrap_cooldown:
        return bool(_AGENT_SECRET)

    _bootstrap_cooldown = now + 60  # cooldown regardless of outcome
    try:
        resp = urllib.request.urlopen(f"{MANAGER_URL}/api/agent/secret", timeout=5)
        data = json.loads(resp.read())
        _AGENT_SECRET = data.get("secret", "")
        if _AGENT_SECRET:
            log.info("Agent secret bootstrapped from manager")
            _bootstrap_cooldown = 0  # success — allow immediate re-bootstrap if needed later
            return True
        else:
            log.warning("Manager returned no secret – will retry in 60s")
            return False
    except Exception as exc:
        log.warning("Failed to bootstrap agent secret: %s – will retry in 60s", exc)
        return False


def _report(service_name: str, replicas: int, cpu_pct: float, mem_pct: float) -> str:
    if not _AGENT_SECRET:
        return "nokey"

    payload = json.dumps({
        "node":         NODE_NAME,
        "service_name": service_name,
        "cpu_pct":      round(cpu_pct, 1),
        "mem_pct":      round(mem_pct, 1),
        "replicas":     replicas,
    }).encode()
    req = urllib.request.Request(
        f"{MANAGER_URL}/api/agent/report",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {_AGENT_SECRET}",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        return "ok"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _AGENT_SECRET = ""  # invalidate — force re-bootstrap
            return "reauth"
        log.debug("Failed to report to manager: HTTP %s", e.code)
        return "ok"
    except Exception as exc:
        log.debug("Failed to report to manager: %s", exc)
        return "ok"


def main():
    log.info("Autoscaler agent starting on node %s", NODE_NAME)
    log.info("  Reporting to %s/api/agent/report", MANAGER_URL)

    _bootstrap()

    sock = "/var/run/docker.sock"
    if not os.path.exists(sock):
        log.error("Docker socket not found at %s – mount it as a volume", sock)
        raise SystemExit(1)
    if not os.access(sock, os.R_OK):
        log.error("Docker socket %s is not readable", sock)
        raise SystemExit(1)

    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        log.error("Cannot connect to Docker daemon: %s", exc)
        raise SystemExit(1)

    _shutdown = threading.Event()

    def _handle_signal(signum, frame):
        log.info("Received signal %d – shutting down", signum)
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    warned: set = set()
    need_bootstrap = False

    while not _shutdown.is_set():
        try:
            services = client.services.list()
        except APIError as exc:
            log.error("Failed to list Swarm services: %s", exc)
            _shutdown.wait(timeout=POLL_INTERVAL)
            continue

        for svc in services:
            try:
                svc.reload()
                labels = svc.attrs.get("Spec", {}).get("Labels", {}) or {}
                cfg = parse_config(svc.name, labels, warned)
                if cfg is None:
                    continue

                name = svc.name
                if name.startswith("autoscaler"):
                    continue

                task_tmpl = svc.attrs.get("Spec", {}).get("TaskTemplate", {})
                res_limits = task_tmpl.get("Resources", {}).get("Limits") or {}
                has_cpu = bool(res_limits.get("NanoCPUs") or res_limits.get("CPUs"))
                has_mem = bool(res_limits.get("MemoryBytes"))
                if not has_cpu and not has_mem:
                    continue

                replicas = current_replicas(svc)
                stats = collect_stats(client, name)

                if stats:
                    result = _report(name, replicas, stats["cpu_pct"], stats["mem_pct"])
                    if result == "reauth":
                        need_bootstrap = True
                    elif result == "nokey":
                        need_bootstrap = True
                    else:
                        log.debug("%s: reported cpu=%.1f%% mem=%.1f%% replicas=%d",
                                  name, stats["cpu_pct"], stats["mem_pct"], replicas)
                else:
                    log.debug("%s: no containers on this node – skipping", name)

            except Exception as exc:
                log.error("Unexpected error processing service %s: %s",
                          svc.name, exc)

        if need_bootstrap:
            log.warning("Agent secret invalid — attempting re-bootstrap")
            if _bootstrap():
                need_bootstrap = False

        _shutdown.wait(timeout=POLL_INTERVAL)

    log.info("Agent shutdown complete")
