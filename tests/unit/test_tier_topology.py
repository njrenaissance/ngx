"""Unit tests for select_az_assignments — pure function, no DB."""

from __future__ import annotations

import pytest

from forge.workers.tier_topology import select_az_assignments


class _AzStub:
    """Minimal RegionAzMap stand-in with the two fields the function reads."""

    def __init__(self, az_index: int, active: bool = True, label: str = "") -> None:
        self.az_index = az_index
        self.active = active
        # Label is unused by the function — present so failures print
        # something human-readable in pytest's repr.
        self.label = label or f"az{az_index}{'!' if not active else ''}"

    def __repr__(self) -> str:
        return f"<{self.label}>"


class TestSelectAzAssignments:
    def test_single_az_returned_as_primary(self) -> None:
        maps = [_AzStub(1)]
        result = select_az_assignments(maps, min_azs=1)
        assert result == [(maps[0], "primary")]

    def test_two_azs_primary_then_secondary_by_index(self) -> None:
        a = _AzStub(2)
        b = _AzStub(1)  # lower index — should become primary
        result = select_az_assignments([a, b], min_azs=2)
        assert [(az.az_index, role) for az, role in result] == [(1, "primary"), (2, "secondary")]

    def test_three_azs_one_primary_two_secondaries(self) -> None:
        maps = [_AzStub(1), _AzStub(2), _AzStub(3)]
        result = select_az_assignments(maps, min_azs=3)
        roles = [role for _, role in result]
        assert roles == ["primary", "secondary", "secondary"]

    def test_takes_only_min_azs_even_if_more_available(self) -> None:
        maps = [_AzStub(1), _AzStub(2), _AzStub(3), _AzStub(4)]
        result = select_az_assignments(maps, min_azs=2)
        assert [az.az_index for az, _ in result] == [1, 2]

    def test_inactive_rows_filtered_before_counting(self) -> None:
        maps = [_AzStub(1, active=False), _AzStub(2), _AzStub(3)]
        result = select_az_assignments(maps, min_azs=2)
        assert [az.az_index for az, _ in result] == [2, 3]

    def test_min_azs_exceeds_active_count_raises(self) -> None:
        maps = [_AzStub(1), _AzStub(2)]  # 2 active
        with pytest.raises(ValueError, match="min_azs=3"):
            select_az_assignments(maps, min_azs=3)

    def test_inactive_count_does_not_satisfy_min(self) -> None:
        """Inactive rows aren't counted toward min_azs — they're invisible."""
        maps = [_AzStub(1, active=False), _AzStub(2, active=False), _AzStub(3)]
        with pytest.raises(ValueError, match="min_azs=2"):
            select_az_assignments(maps, min_azs=2)

    def test_min_azs_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="min_azs must be >= 1"):
            select_az_assignments([_AzStub(1)], min_azs=0)

    def test_min_azs_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="min_azs must be >= 1"):
            select_az_assignments([_AzStub(1)], min_azs=-1)

    def test_deterministic_when_input_shuffled(self) -> None:
        """Selection is a stable function of az_index regardless of input order."""
        shuffled = [_AzStub(3), _AzStub(1), _AzStub(4), _AzStub(2)]
        result = select_az_assignments(shuffled, min_azs=3)
        assert [az.az_index for az, _ in result] == [1, 2, 3]
