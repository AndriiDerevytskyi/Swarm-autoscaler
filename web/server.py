import json
import os
import queue
import secrets
import threading
from datetime import datetime
from typing import Dict, Set

import docker
from docker.errors import DockerException
from flask import Flask, Response, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash

from core.database import (
    auth_is_configured,
    auth_set_password,
    auth_verify,
    clear_events,
    get_events,
    get_replica_history,
    meta_get,
    meta_set,
    meta_set_batch,
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
# werkzeug logging is configured in core/logging.py

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
    agent_secret: str = "",
    session_secret: str = "",
) -> None:
    global _config
    app.secret_key = session_secret or secrets.token_hex(32)
    version = "dev"
    try:
        with open("/app/VERSION") as f:
            version = f.read().strip()
    except Exception:
        pass
    _config = {
        "log_level":      log_level,
        "poll_interval":  poll_interval,
        "web_port":       web_port,
        "version":        version,
        "label_defaults": label_defaults,
        "_agent_secret": agent_secret,
    }


def update_service(name: str, data: dict) -> None:
    with _lock:
        _services[name] = data


def remove_service(name: str) -> None:
    with _lock:
        _services.pop(name, None)


def managed_names() -> set:
    with _lock:
        return set(_services.keys())


def set_docker_health(ok: bool) -> None:
    global _docker_ok
    _docker_ok = ok


def start(host: str = "0.0.0.0", port: int = 8080) -> None:
    import werkzeug.serving as _ws
    _orig_log = _ws._log
    def _quiet_log(level, msg, *args):
        if level not in ("info",):
            _orig_log(level, msg, *args)
    _ws._log = _quiet_log
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        _ws._log = _orig_log


# ── SSE (Server-Sent Events) ───────────────────────────────────────────────

_sse_clients: Set[queue.Queue] = set()
_sse_lock = threading.Lock()


def _sse_payload() -> str:
    with _lock:
        services = [_serial(s) for s in _services.values()]
    events = get_events(50)
    return json.dumps({"services": services, "events": events, "docker_ok": _docker_ok}, default=str)


def broadcast_sse() -> None:
    global _sse_clients
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
    if request.path in ("/api/stream", "/api/health", "/api/agent/report",
                         "/api/agent/secret", "/api/agent/managed",
                         "/api/auth/status", "/api/auth/setup",
                         "/api/auth/login", "/api/auth/logout"):
        return

    if request.path == "/api/metrics":
        if not _metrics_enabled():
            return Response("", 404)
        auth = request.authorization
        if not auth or auth.username != _metrics_username() or not _metrics_verify(auth.password):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Swarm Autoscaler Metrics"'},
            )
        return

    if not auth_is_configured():
        return

    if not session.get("user"):
        return Response("Authentication required", 401)


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


# ── metrics auth helpers ──────────────────────────────────────────────────

def _metrics_enabled() -> bool:
    return meta_get("metrics_enabled") == "1"

def _metrics_username() -> str:
    return meta_get("metrics_user")

def _metrics_password_hash() -> str:
    return meta_get("metrics_password_hash")

def _metrics_verify(password: str) -> bool:
    phash = _metrics_password_hash()
    if not phash:
        return False
    return check_password_hash(phash, password)


# ── routes ────────────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path: str = ""):
    return send_from_directory(app.template_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": _docker_ok})


@app.get("/api/auth/status")
def auth_status():
    return jsonify({
        "configured":    auth_is_configured(),
        "authenticated": bool(session.get("user")),
    })


@app.post("/api/auth/setup")
def auth_setup():
    if auth_is_configured():
        return jsonify({"ok": False, "error": "already configured"}), 409

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "username and password required"}), 400
    if len(password) < 4:
        return jsonify({"ok": False, "error": "password must be at least 4 characters"}), 400

    auth_set_password(username, password)
    return jsonify({"ok": True})


@app.post("/api/auth/login")
def auth_login():
    if not auth_is_configured():
        return jsonify({"ok": False, "error": "no user configured"}), 400

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "username and password required"}), 400

    if not auth_verify(username, password):
        return jsonify({"ok": False, "error": "invalid credentials"}), 401

    session["user"] = username
    return jsonify({"ok": True, "username": username})


@app.post("/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/auth/change")
def auth_change():
    if not auth_is_configured():
        return jsonify({"ok": False, "error": "no user configured"}), 400

    current_user = session.get("user", "")
    if not current_user:
        return jsonify({"ok": False, "error": "not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    current  = (data.get("current_password") or "").strip()
    new_pass = (data.get("new_password") or "").strip()
    if not current or not new_pass:
        return jsonify({"ok": False, "error": "current and new password required"}), 400
    if len(new_pass) < 4:
        return jsonify({"ok": False, "error": "password must be at least 4 characters"}), 400

    if not auth_verify(current_user, current):
        return jsonify({"ok": False, "error": "current password is incorrect"}), 403

    auth_set_password(current_user, new_pass)
    return jsonify({"ok": True})


@app.get("/api/metrics/auth/status")
def metrics_auth_status():
    enabled = _metrics_enabled()
    return jsonify({
        "enabled":  enabled,
        "username": _metrics_username() if enabled else "",
    })


@app.post("/api/metrics/auth/enable")
def metrics_auth_enable():
    import secrets
    user = (request.get_json(silent=True) or {}).get("username", "prometheus").strip() or "prometheus"
    password = secrets.token_hex(16)
    from werkzeug.security import generate_password_hash
    phash = generate_password_hash(password)
    meta_set_batch({
        "metrics_enabled": "1",
        "metrics_user": user,
        "metrics_password_hash": phash,
    })
    return jsonify({"ok": True, "username": user, "password": password})


@app.post("/api/metrics/auth/disable")
def metrics_auth_disable():
    meta_set("metrics_enabled", "0")
    return jsonify({"ok": True})


@app.post("/api/metrics/auth/regenerate")
def metrics_auth_regenerate():
    if not _metrics_enabled():
        return jsonify({"ok": False, "error": "metrics auth not enabled"}), 400
    import secrets
    password = secrets.token_hex(16)
    from werkzeug.security import generate_password_hash
    phash = generate_password_hash(password)
    meta_set("metrics_password_hash", phash)
    return jsonify({"ok": True, "username": _metrics_username(), "password": password})


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


@app.get("/api/agent/managed")
def agent_managed():
    secret = _config.get("_agent_secret", "")
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    with _lock:
        names = [s["name"] for s in _services.values()]
    return jsonify({"services": names})


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
