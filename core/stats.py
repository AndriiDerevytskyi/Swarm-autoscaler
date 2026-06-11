from typing import Optional

import docker

from core.logging import log


def collect_stats(client: docker.DockerClient, service_name: str) -> Optional[dict]:
    containers = client.containers.list(
        filters={"label": f"com.docker.swarm.service.name={service_name}"}
    )
    if not containers:
        return None

    cpu_samples, mem_samples = [], []

    for ctr in containers:
        try:
            raw = ctr.stats(stream=False)

            cpu_delta = (
                raw["cpu_stats"]["cpu_usage"]["total_usage"]
                - raw["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            sys_delta = (
                raw["cpu_stats"].get("system_cpu_usage", 0)
                - raw["precpu_stats"].get("system_cpu_usage", 0)
            )
            n_cpus = raw["cpu_stats"].get("online_cpus") or len(
                raw["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
            )
            cpu_pct = (cpu_delta / sys_delta * n_cpus * 100.0) if sys_delta > 0 else 0.0

            mem_usage = raw["memory_stats"].get("usage", 0)
            mem_limit = raw["memory_stats"].get("limit", 1)
            mem_stats = raw["memory_stats"].get("stats", {})
            cache     = mem_stats.get("inactive_file", mem_stats.get("cache", 0))
            mem_pct   = max(0.0, (mem_usage - cache) / mem_limit * 100.0) if mem_limit else 0.0

            log.debug("%s [%s]  cpu=%.1f%%  mem=%.1f%%",
                      service_name, ctr.short_id, cpu_pct, mem_pct)

            cpu_samples.append(cpu_pct)
            mem_samples.append(mem_pct)

        except Exception as exc:
            log.warning("%s [%s]: failed to read stats – %s",
                        service_name, ctr.short_id, exc)

    if not cpu_samples:
        return None

    return {
        "cpu_pct": min(100.0, sum(cpu_samples) / len(cpu_samples)),
        "mem_pct": min(100.0, sum(mem_samples) / len(mem_samples)),
    }


def current_replicas(service) -> int:
    return service.attrs["Spec"]["Mode"].get("Replicated", {}).get("Replicas", 1)
