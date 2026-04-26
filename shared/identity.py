"""Identity middleware, POC version.

Returns a hardcoded demo user/group. Swap this implementation when Entra arrives;
all writers and readers go through this module so the swap is one place.
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.config import get_settings


@dataclass(frozen=True)
class Identity:
    user_id: str
    group_id: str


def current_identity() -> Identity:
    s = get_settings()
    return Identity(user_id=s.demo_user_id, group_id=s.demo_group_id)
