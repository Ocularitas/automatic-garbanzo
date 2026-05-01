"""Tests for the health-check probe contract.

We don't reach a real DB or hit Anthropic / Voyage in unit tests.
What we verify:

  - The `Probe` dataclass shape is what callers expect.
  - `run_all_probes()` returns the documented top-level shape.
  - Skip semantics are correct when API keys are unset.

Integration verification of the actual probes runs on the VM, where the
deps are real.
"""
from __future__ import annotations

from shared.healthcheck import (
    Probe,
    probe_anthropic,
    probe_voyage,
    run_all_probes,
)


def test_probe_dataclass_shape() -> None:
    p = Probe(name="x", status="ok", latency_ms=10, message="up")
    assert p.name == "x"
    assert p.status == "ok"
    assert p.latency_ms == 10
    assert p.message == "up"


def test_run_all_probes_returns_documented_shape(monkeypatch) -> None:
    # Make sure neither API key is set so we don't hit the network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    result = run_all_probes()
    assert "ok" in result
    assert "probes" in result
    assert isinstance(result["probes"], list)
    assert len(result["probes"]) == 3
    names = {p["name"] for p in result["probes"]}
    assert names == {"postgres", "anthropic", "voyage"}
    for p in result["probes"]:
        assert set(p) == {"name", "status", "latency_ms", "message"}
        assert p["status"] in {"ok", "fail", "skip"}


def test_anthropic_probe_skips_when_key_unset(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    p = probe_anthropic()
    assert p.status == "skip"
    assert "not set" in p.message.lower()


def test_voyage_probe_skips_when_key_unset(monkeypatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    p = probe_voyage()
    assert p.status == "skip"
    assert "not set" in p.message.lower()


def test_overall_not_ok_when_any_probe_fails_or_skips(monkeypatch) -> None:
    """Skipped probes don't count toward overall ok=true.

    The reasoning: a probe that's skipped because its key is unset is a
    misconfiguration we want surfaced, not a passing run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    result = run_all_probes()
    assert result["ok"] is False
