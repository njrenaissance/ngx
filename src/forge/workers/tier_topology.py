"""AZ selection for tier-aware workspace materialization.

Pure function — no DB session, no I/O. Picks `min_azs` AZ maps from a region,
assigns the lowest-index one as `primary` and the rest as `secondary`.
Active-only filtering happens here so callers can pass a raw query result
without pre-filtering.
"""

from __future__ import annotations

from forge.models.topology import RegionAzMap


def select_az_assignments(
    region_az_maps: list[RegionAzMap],
    min_azs: int,
) -> list[tuple[RegionAzMap, str]]:
    """Pick `min_azs` active AZs ordered by az_index ascending.

    The first picked AZ is assigned role "primary"; the remainder are
    "secondary". Inactive rows are filtered out before counting.

    Why ordering matters: the worker writes one DEPLOYMENT_AZ row per
    selection, and SPEC §8.3 requires the primary/secondary split to be
    stable across re-runs so an idempotent retry doesn't shuffle roles.
    Ordering by az_index gives that determinism without requiring a stored
    selection record.

    Raises:
        ValueError: if `min_azs` < 1, or if fewer than `min_azs` active
        AZs are available — both are programmer-error conditions that
        callers shouldn't recover from at this layer.
    """
    if min_azs < 1:
        raise ValueError(f"min_azs must be >= 1, got {min_azs}")

    active_sorted = sorted(
        (m for m in region_az_maps if m.active),
        key=lambda m: m.az_index,
    )
    if len(active_sorted) < min_azs:
        raise ValueError(f"region has {len(active_sorted)} active AZ(s); tier requires min_azs={min_azs}")

    picked = active_sorted[:min_azs]
    return [(az, "primary" if i == 0 else "secondary") for i, az in enumerate(picked)]
