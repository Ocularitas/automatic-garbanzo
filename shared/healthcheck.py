"""Health-check probes for the system's external dependencies.

Three probes are run in sequence: Postgres reachability + the `vector`
extension being installed, Anthropic API key validity, Voyage API key
validity. Each returns a `Probe` with status + latency + a human-readable
message. The overall result is `ok` if every probe passed.

Designed to be invoked from:
  - `uv run health-check` (CLI, exit code 0/1, JSON output)
  - APIM / k8s liveness probes (just consume the JSON)
  - manual debugging on the VM

The probes deliberately do not cost real API tokens — they hit metadata
endpoints / make minimal calls. Anthropic gets a 1-token completion;
Voyage gets a 1-character embedding. Pennies-per-thousand-runs.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Literal

from shared.config import get_settings

ProbeStatus = Literal["ok", "fail", "skip"]


@dataclass
class Probe:
    name: str
    status: ProbeStatus
    latency_ms: int
    message: str


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def probe_postgres() -> Probe:
    """Reach Postgres, run `SELECT 1`, and confirm the `vector` extension
    is installed (which means the schema can use embedding columns)."""
    start = time.perf_counter()
    try:
        from sqlalchemy import text

        from shared.db import session_scope

        with session_scope() as s:
            s.execute(text("SELECT 1")).scalar()
            extensions = {
                row[0] for row in s.execute(
                    text("SELECT extname FROM pg_extension")
                )
            }
        if "vector" not in extensions:
            return Probe(
                name="postgres",
                status="fail",
                latency_ms=_ms_since(start),
                message="Connected, but `vector` extension is not installed.",
            )
        return Probe(
            name="postgres",
            status="ok",
            latency_ms=_ms_since(start),
            message="Connected; vector extension present.",
        )
    except Exception as e:
        return Probe(
            name="postgres",
            status="fail",
            latency_ms=_ms_since(start),
            message=f"{type(e).__name__}: {e}",
        )


def probe_anthropic() -> Probe:
    """Verify the Anthropic API key by issuing a 1-token completion."""
    start = time.perf_counter()
    settings = get_settings()
    if not settings.anthropic_api_key:
        return Probe(
            name="anthropic",
            status="skip",
            latency_ms=0,
            message="ANTHROPIC_API_KEY not set; skipped.",
        )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # Tiny, deterministic call — costs ~$0.0001
        client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return Probe(
            name="anthropic",
            status="ok",
            latency_ms=_ms_since(start),
            message=f"Authenticated; model={settings.anthropic_model}",
        )
    except Exception as e:
        return Probe(
            name="anthropic",
            status="fail",
            latency_ms=_ms_since(start),
            message=f"{type(e).__name__}: {e}",
        )


def probe_voyage() -> Probe:
    """Verify the Voyage API key by embedding a 1-char query."""
    start = time.perf_counter()
    settings = get_settings()
    if not settings.voyage_api_key:
        return Probe(
            name="voyage",
            status="skip",
            latency_ms=0,
            message="VOYAGE_API_KEY not set; skipped.",
        )
    try:
        import voyageai

        client = voyageai.Client(api_key=settings.voyage_api_key)
        result = client.embed(
            texts=["x"],
            model=settings.voyage_embedding_model,
            input_type="query",
        )
        dims = len(result.embeddings[0])
        if dims != settings.voyage_embedding_dimensions:
            return Probe(
                name="voyage",
                status="fail",
                latency_ms=_ms_since(start),
                message=(
                    f"Embedding dimension mismatch: API returned {dims}, "
                    f"config expects {settings.voyage_embedding_dimensions}. "
                    "Re-embedding required."
                ),
            )
        return Probe(
            name="voyage",
            status="ok",
            latency_ms=_ms_since(start),
            message=f"Authenticated; model={settings.voyage_embedding_model} dims={dims}",
        )
    except Exception as e:
        return Probe(
            name="voyage",
            status="fail",
            latency_ms=_ms_since(start),
            message=f"{type(e).__name__}: {e}",
        )


def run_all_probes() -> dict:
    """Run every probe; return a structured result dict.

    Shape:
      {
        "ok": bool,
        "probes": [{"name", "status", "latency_ms", "message"}, ...],
      }

    `ok` is true only if every probe is `ok` (skipped probes are not
    failures but also don't count toward `ok=true`; a probe that's
    skipped because its API key is unset returns ok=false to make the
    misconfiguration visible)."""
    probes = [probe_postgres(), probe_anthropic(), probe_voyage()]
    overall_ok = all(p.status == "ok" for p in probes)
    return {
        "ok": overall_ok,
        "probes": [asdict(p) for p in probes],
    }
