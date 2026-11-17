"""Load the GujjuTours dashboard into the local Grafana (observability/docker-compose.yml).

    python observability/load_dashboard.py [--url http://127.0.0.1:3005] [--auth admin:admin]

Panels: chat turns and their duration, tool calls, supplier calls and errors by
endpoint and code, AI model calls, latency and tokens, recent traces, and the
API's logs (each log line links to its trace).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

DASHBOARD = Path(__file__).with_name("grafana-dashboard.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:3005")
    ap.add_argument("--auth", default="admin:admin", help="user:password (local or self-hosted)")
    ap.add_argument("--token", help="Grafana service-account token (Grafana Cloud); used instead of --auth")
    a = ap.parse_args()
    dash = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    kwargs: dict = {"headers": {"Authorization": f"Bearer {a.token}"}} if a.token else {"auth": tuple(a.auth.partition(":")[::2])}
    r = httpx.post(f"{a.url.rstrip('/')}/api/dashboards/db", timeout=30,
                   json={"dashboard": dash, "overwrite": True, "folderId": 0}, **kwargs)
    if r.status_code != 200:
        print("failed:", r.status_code, r.text[:300])
        return 1
    print("dashboard:", a.url + r.json().get("url", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
