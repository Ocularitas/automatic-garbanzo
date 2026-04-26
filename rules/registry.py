"""Rule registry. Loads rule modules and resolves folder paths to rules."""
from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

from shared.config import get_settings
from shared.models import Rule

# Rule ids the registry knows how to load. Adding a rule = create
# rules/<rule_id>/__init__.py exposing RULE, and add it here.
KNOWN_RULES: tuple[str, ...] = (
    "saas_contract",
    "services_contract",
    "lease",
    "generic_contract",
)


@lru_cache(maxsize=1)
def all_rules() -> dict[str, Rule]:
    """All registered rules, keyed by rule_id."""
    out: dict[str, Rule] = {}
    for rule_id in KNOWN_RULES:
        module = importlib.import_module(f"rules.{rule_id}")
        rule = getattr(module, "RULE")
        if rule.rule_id != rule_id:
            raise RuntimeError(
                f"rules/{rule_id}/__init__.py exposes RULE.rule_id={rule.rule_id!r}, "
                f"expected {rule_id!r}"
            )
        out[rule_id] = rule
    return out


def get_rule(rule_id: str) -> Rule:
    rules = all_rules()
    if rule_id not in rules:
        raise KeyError(f"Unknown rule_id: {rule_id}")
    return rules[rule_id]


@lru_cache(maxsize=1)
def folder_map() -> dict[str, str]:
    """Loaded folder_map.yaml. Keys are folder prefixes, values are rule_ids."""
    path = get_settings().rules_dir / "folder_map.yaml"
    with path.open() as f:
        data = yaml.safe_load(f)
    return data


def resolve_rule_for_path(file_path: Path) -> Rule:
    """Resolve which rule applies to a file based on its path under the watch folder.

    Uses longest-prefix matching against `folder_map.yaml`. Falls back to `default`.
    """
    settings = get_settings()
    fmap = folder_map()
    try:
        rel = file_path.resolve().relative_to(settings.watch_folder.resolve())
    except ValueError:
        # File is outside the watch folder (e.g. one-shot CLI). Use the file's
        # full path for matching anyway.
        rel = file_path

    rel_posix = PurePosixPath(*rel.parts)
    rel_str = str(rel_posix)

    folders: dict[str, str] = fmap.get("folders") or {}
    best: tuple[int, str] | None = None
    for prefix, rule_id in folders.items():
        if rel_str == prefix or rel_str.startswith(prefix + "/"):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), rule_id)

    rule_id = best[1] if best else fmap.get("default", "generic_contract")
    return get_rule(rule_id)
