import os
from typing import Optional, Set

from core.logging import LOG_LEVEL, log

POLL_INTERVAL = int(os.getenv("AUTOSCALER_POLL_INTERVAL", "15"))
WEB_PORT      = int(os.getenv("AUTOSCALER_WEB_PORT",      "8080"))

LABEL_DEFAULTS = {
    "swarm.autoscaler.min_replicas":  os.getenv("AUTOSCALER_DEFAULT_MIN_REPLICAS",  "1"),
    "swarm.autoscaler.max_replicas":  os.getenv("AUTOSCALER_DEFAULT_MAX_REPLICAS",  "5"),
    "swarm.autoscaler.cpu.threshold": os.getenv("AUTOSCALER_DEFAULT_CPU_THRESHOLD", "80"),
    "swarm.autoscaler.ram.threshold": os.getenv("AUTOSCALER_DEFAULT_RAM_THRESHOLD", "80"),
    "swarm.autoscaler.cooldown":      os.getenv("AUTOSCALER_DEFAULT_COOLDOWN",      "5"),
}


def parse_config(
    service_name: str,
    labels: dict,
    warned: Set[tuple],
) -> Optional[dict]:
    if labels.get("swarm.autoscaler.enable") != "true":
        return None

    resolved = {}
    for key, default in LABEL_DEFAULTS.items():
        if key not in labels:
            marker = (service_name, key)
            if marker not in warned:
                log.warning(
                    "%s: label '%s' not set – using default value '%s'",
                    service_name, key, default,
                )
                warned.add(marker)
        resolved[key] = labels.get(key, default)

    return {
        "min_replicas":    int(resolved["swarm.autoscaler.min_replicas"]),
        "max_replicas":    int(resolved["swarm.autoscaler.max_replicas"]),
        "cpu_threshold":   float(resolved["swarm.autoscaler.cpu.threshold"]),
        "ram_threshold":   float(resolved["swarm.autoscaler.ram.threshold"]),
        "cooldown_minutes": int(resolved["swarm.autoscaler.cooldown"]),
    }
