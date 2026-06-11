#!/usr/bin/env python3
"""Healthcheck for Swarm Autoscaler — works for both manager and agent roles."""

import os
import sys
import urllib.request
import urllib.error


def check_manager(port: int) -> bool:
    """Check the /api/health endpoint with a timeout and retry."""
    url = f"http://127.0.0.1:{port}/api/health"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=3)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return False


def main():
    role = os.getenv("AUTOSCALER_ROLE", "manager")

    if role == "agent":
        # Agent is stateless — healthy as long as the process runs.
        # If the process dies, Docker catches it via the exit code
        # and restart_policy handles recovery.
        sys.exit(0)

    # Manager: check the web endpoint
    port = int(os.getenv("AUTOSCALER_WEB_PORT", "8080"))
    if check_manager(port):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
