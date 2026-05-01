"""`uv run health-check` entry point.

Outputs the probe results as JSON to stdout. Exits 0 if all probes
returned `ok`, 1 otherwise. Suitable for APIM HTTP probes (when wrapped
in a small server), k8s liveness probes, systemd timers, or manual
inspection on the VM."""
from __future__ import annotations

import json
import sys

from shared.healthcheck import run_all_probes
from shared.logging import configure_logging


def main() -> None:
    configure_logging()
    result = run_all_probes()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
