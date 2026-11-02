from datetime import date

import pytest
from opagent_core.lunar import lunar_new_year


@pytest.mark.unit
@pytest.mark.tc("CORE-LUNAR-001")
def test_core_lunar_001_table():
    assert lunar_new_year(2024) == date(2024, 2, 10)
    assert lunar_new_year(2025) == date(2025, 1, 29)
    assert lunar_new_year(2026) == date(2026, 2, 17)
    assert lunar_new_year(2030) == date(2030, 2, 3)
    assert all(lunar_new_year(y) is not None for y in range(1900, 2100))
    assert lunar_new_year(1899) is None and lunar_new_year(2100) is None
    for y in range(1900, 2100):  # always between Jan 21 and Feb 20
        d = lunar_new_year(y)
        assert date(y, 1, 21) <= d <= date(y, 2, 20)
