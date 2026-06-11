import json
import os
import signal
import socket
import threading
import time
import urllib.error
import urllib.request

import docker
from docker.errors import DockerException

from core.logging import log
from core.stats import collect_stats

MANAGER_URL = os.getenv("AUTOSCALER_MANAGER_URL", "http://autoscaler:8080")
POLL_INTERVAL = int(os.getenv("AUTOSCALER_POLL_INTERVAL", "15"))
NODE_NAME     = os.getenv("AUTOSCALER_NODE_NAME", socket.gethostname())

_AGENT_SECRET       = ""
_bootstrap_cooldown  = 0.0


def _call_manager(path: str, timeout: int = 5):
    headers = {}
    if _AGENT_SECRET:
        headers["Authorization"] = f"Bearer {_AGENT_SECRET}"
    req = urllib.request.Request(f"{MANAGER_URL}{path}", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None
        raise


def _bootstrap() -> bool:
    global _AGENT_SECRET, _bootstrap_cooldown
    now = time.time()
    if now < _bootstrap_cooldown:
        return bool(_AGENT_SECRET)

    try:
        data = _call_manager("/api/agent/secret")
        if data and data.get("secret"):
            _AGENT_SECRET = data["secret"]
            _bootstrap_cooldown = 0
            log.info("Agent secret bootstrapped from manager")
            return True
        else:
            _bootstrap_cooldown = now + 60
            log.warning("Manager returned no secret – will retry in 60s")
            return False
    except Exception as exc:
        _bootstrap_cooldown = now + 10  # short cooldown for connection/DNS errors
        log.warning("Failed to bootstrap agent secret: %s – will retry in 10s", exc)
        return False


def _fetch_managed() -> list:
    global _AGENT_SECRET
    try:
        data = _call_manager("/api/agent/managed")
        if data is None:
            global _AGENT_SECRET
            _AGENT_SECRET = ""
            return []
        return data.get("services", [])
    except Exception as exc:
        log.warning("Failed to fetch managed services: %s", exc)
        return []


def _report(service_name: str, replicas: int, cpu_pct: float, mem_pct: float) -> str:
    global _AGENT_SECRET
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
            _AGENT_SECRET = ""
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

    managed = []
    need_bootstrap = False

    while not _shutdown.is_set():
        if not managed or need_bootstrap:
            managed = _fetch_managed()
            need_bootstrap = False

        for name in managed:
            try:
                if name.startswith("autoscaler"):
                    continue

                stats = collect_stats(client, name)
                if stats:
                    ctrs = client.containers.list(
                        filters={"label": f"com.docker.swarm.service.name={name}"}
                    )
                    result = _report(name, len(ctrs), stats["cpu_pct"], stats["mem_pct"])
                    if result == "reauth":
                        need_bootstrap = True
                        _bootstrap()
                        managed = _fetch_managed()
                        break
                    elif result == "nokey":
                        need_bootstrap = True
                        continue
                    else:
                        log.debug("%s: reported cpu=%.1f%% mem=%.1f%% replicas=%d",
                                  name, stats["cpu_pct"], stats["mem_pct"], len(ctrs))
                else:
                    log.debug("%s: no containers on this node – skipping", name)

            except Exception as exc:
                log.error("Unexpected error processing service %s: %s", name, exc)

        _shutdown.wait(timeout=POLL_INTERVAL)

    log.info("Agent shutdown complete")
