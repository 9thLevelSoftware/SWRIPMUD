"""Tier module registry and loader for SWRIPMUD E2E test suites."""

from __future__ import annotations

import importlib

TIER_MODULES: tuple[str, ...] = (
    "tier1_f01_09",
    "tier1_f10_18",
    "tier2_f01_09",
    "tier2_f10_18",
    "tier3_pairwise",
    "tier4_workloads",
)


def load_all_tiers() -> list[str]:
    """Import every tier module and propagate discovery failures."""
    loaded: list[str] = []
    for mod_name in TIER_MODULES:
        fq_name = f"{__name__}.{mod_name}"
        importlib.import_module(fq_name)
        loaded.append(fq_name)
    return loaded


__all__ = ["TIER_MODULES", "load_all_tiers"]
