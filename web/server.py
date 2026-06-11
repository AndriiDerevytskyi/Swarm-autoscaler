import json
import os
import queue
import threading
import logging
from datetime import datetime
from typing import Dict, Set

import docker
from docker.errors import DockerException
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash

from core.database import (
    clear_events,
    get_events,
    get_replica_history,
    pause_service,
    record_event,
    record_node_metrics,
    resume_service,
)

_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(_DIR, "static"),
    static_url_path="/static",
    template_folder=os.path.join(_DIR, "templates"),
)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_lock = threading.Lock()
_services: Dict[str, dict] = {}
_config: dict = {}
_docker_ok: bool = True


# ── called by main.py on startup ──────────────────────────────────────────

def setup(
    log_level: str,
    poll_interval: int,
    web_port: int,
    label_defaults: dict,
    auth_user: str = "",
    auth_hash: str = "",
    metrics_user: str = "",
    metrics_hash: str = "",
    agent_secret: str = "",
) -> None:
    global _config
    _config = {
        "log_level":      log_level,
        "poll_interval":  poll_interval,
        "web_port":       web_port,
        "label_defaults": label_defaults,
        "_auth_user":    auth_user,
        "_auth_hash":    auth_hash,
        "_metrics_user": metrics_user,
        "_metrics_hash": metrics_hash,
        "_agent_secret": agent_secret,
    }


def update_service(name: str, data: dict) -> None:
    with _lock:
        _services[name] = data


def remove_service(name: str) -> None:
    with _lock:
        _services.pop(name, None)


def set_docker_health(ok: bool) -> None:
    global _docker_ok
    _docker_ok = ok


def start(host: str = "0.0.0.0", port: int = 8080) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


# ── SSE (Server-Sent Events) ───────────────────────────────────────────────

_sse_clients: Set[queue.Queue] = set()
_sse_lock = threading.Lock()


def _sse_payload() -> str:
    with _lock:
        services = [_serial(s) for s in _services.values()]
    events = get_events(50)
    return json.dumps({"services": services, "events": events, "docker_ok": _docker_ok}, default=str)


def broadcast_sse() -> None:
    payload = _sse_payload()
    with _sse_lock:
        dead = set()
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.add(q)
        _sse_clients -= dead


@app.route("/api/stream")
def stream():
    def generate():
        q = queue.Queue(maxsize=8)
        # send initial state immediately
        try:
            q.put_nowait(_sse_payload())
        except queue.Full:
            pass
        with _sse_lock:
            _sse_clients.add(q)
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with _sse_lock:
                _sse_clients.discard(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── auth ──────────────────────────────────────────────────────────────────

@app.before_request
def _require_auth():
    if request.path == "/api/stream":
        return

    if request.path == "/api/health":
        return

    if request.path == "/api/agent/report":
        return

    if request.path == "/api/metrics":
        user  = _config.get("_metrics_user", "")
        phash = _config.get("_metrics_hash", "")
        if not user or not phash:
            return

        auth = request.authorization
        if (
            not auth
            or auth.username != user
            or not check_password_hash(phash, auth.password)
        ):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Swarm Autoscaler Metrics"'},
            )
        return

    user  = _config.get("_auth_user", "")
    phash = _config.get("_auth_hash", "")
    if not user or not phash:
        return

    auth = request.authorization
    if (
        not auth
        or auth.username != user
        or not check_password_hash(phash, auth.password)
    ):
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Swarm Autoscaler"'},
        )


# ── helpers ───────────────────────────────────────────────────────────────

def _serial(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _serial(v)
        elif isinstance(v, list):
            out[k] = [
                _serial(item) if isinstance(item, dict) else
                item.isoformat() if isinstance(item, datetime) else item
                for item in v
            ]
        else:
            out[k] = v
    return out


# ── routes ────────────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path: str = ""):
    return send_from_directory(app.template_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": _docker_ok})


@app.post("/api/agent/report")
def agent_report():
    secret = _config.get("_agent_secret", "")
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    try:
        node         = data["node"]
        service_name = data["service_name"]
        cpu_pct      = float(data["cpu_pct"])
        mem_pct      = float(data["mem_pct"])
        replicas     = int(data["replicas"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    record_node_metrics(node, service_name, cpu_pct, mem_pct, replicas)
    return jsonify({"ok": True})


@app.get("/api/agent/secret")
def agent_bootstrap():
    secret = _config.get("_agent_secret", "")
    if not secret:
        return jsonify({"secret": None}), 404
    src = request.remote_addr or ""
    if not (src.startswith("10.") or src.startswith("172.") or src.startswith("192.168.") or src == "127.0.0.1"):
        return jsonify({"secret": None}), 403
    return jsonify({"secret": secret})


@app.get("/api/services")
def get_services():
    with _lock:
        return jsonify([_serial(s) for s in _services.values()])


@app.get("/api/config")
def get_config():
    return jsonify({k: v for k, v in _config.items() if not k.startswith("_")})


@app.get("/api/metrics")
def metrics():
    lines = []
    with _lock:
        for svc in _services.values():
            lines.append(f'autoscaler_replicas{{service="{svc["name"]}"}} {svc["replicas"]}')
            lines.append(f'autoscaler_cpu_pct{{service="{svc["name"]}"}} {svc.get("cpu_pct", 0):.1f}')
            lines.append(f'autoscaler_mem_pct{{service="{svc["name"]}"}} {svc.get("mem_pct", 0):.1f}')
            lines.append(f'autoscaler_cpu_threshold{{service="{svc["name"]}"}} {svc.get("cpu_threshold", 0)}')
            lines.append(f'autoscaler_ram_threshold{{service="{svc["name"]}"}} {svc.get("ram_threshold", 0)}')
            lines.append(f'autoscaler_paused{{service="{svc["name"]}"}} {1 if svc.get("paused") else 0}')
    lines.append(f"autoscaler_docker_ok {1 if _docker_ok else 0}")
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.post("/api/services/<name>/scale")
def scale_service(name: str):
    data = request.get_json(silent=True) or {}
    try:
        replicas = int(data["replicas"])
        if replicas < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "invalid replicas value"}), 400

    with _lock:
        svc = _services.get(name, {})
        min_r = svc.get("min_replicas", 0)
        max_r = svc.get("max_replicas", replicas) if replicas > 0 else replicas
        if replicas < min_r or replicas > max_r:
            return jsonify({
                "ok": False,
                "error": f"replicas must be between {min_r} and {max_r}",
            }), 400

    try:
        client = docker.from_env()
        for svc in client.services.list(filters={"name": name}):
            if svc.name == name:
                current = svc.attrs["Spec"]["Mode"].get("Replicated", {}).get("Replicas", 0)
                svc.scale(replicas)
                record_event(name, "manual", current, replicas, "manual override via web UI")

                with _lock:
                    if name in _services:
                        _services[name]["replicas"] = replicas
                        _services[name]["last_action"] = "manual"
                        _services[name]["last_scale_at"] = datetime.now().isoformat()
                return jsonify({"ok": True, "replicas": replicas})
        return jsonify({"ok": False, "error": f"service '{name}' not found"}), 404
    except DockerException as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/services/<name>/pause")
def api_pause_service(name: str):
    data = request.get_json(silent=True) or {}
    duration = data.get("duration", 0)
    pause_service(name, duration)
    with _lock:
        if name in _services:
            _services[name]["paused"] = True
            alerts = list(_services[name].get("alerts", []))
            if "Autoscaling paused" not in alerts:
                alerts.append("Autoscaling paused")
            _services[name]["alerts"] = alerts
    return jsonify({"ok": True, "paused": True})


@app.post("/api/services/<name>/resume")
def api_resume_service(name: str):
    resume_service(name)
    with _lock:
        if name in _services:
            _services[name]["paused"] = False
            alerts = [a for a in _services[name].get("alerts", []) if a != "Autoscaling paused"]
            _services[name]["alerts"] = alerts
    return jsonify({"ok": True, "paused": False})


@app.get("/api/events")
def api_get_events():
    limit = request.args.get("limit", 50, type=int)
    service = request.args.get("service", "", type=str)
    return jsonify(get_events(limit, service))


@app.delete("/api/events")
def api_clear_events():
    service = request.args.get("service", "", type=str)
    count = clear_events(service)
    return jsonify({"ok": True, "deleted": count})


@app.get("/api/services/<name>/history")
def api_get_history(name: str):
    minutes = request.args.get("minutes", 60, type=int)
    return jsonify(get_replica_history(name, minutes))
