"""OS power source for the resource policy (06 §5, DESK-023): pause heavy work on battery, resume on AC."""

import pytest
from opagent_worker.policy import ResourcePolicy
from opagent_worker.power import SysfsPower


def _supply(root, name, typ, online=None, status=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "type").write_text(typ + "\n", encoding="utf-8")
    if online is not None:
        (d / "online").write_text(f"{online}\n", encoding="utf-8")
    if status is not None:
        (d / "status").write_text(status + "\n", encoding="utf-8")


@pytest.mark.unit
@pytest.mark.tc("DESK-023")
def test_desk_023_unplugging_pauses_and_replugging_resumes(tmp_path):
    _supply(tmp_path, "AC", "Mains", online=1)
    _supply(tmp_path, "BAT0", "Battery", status="Charging")
    power = SysfsPower(tmp_path)
    pol = ResourcePolicy(power=power)
    assert power.on_ac and pol.paused_reason() is None
    (tmp_path / "AC" / "online").write_text("0\n", encoding="utf-8")  # unplugged: read live on every check
    (tmp_path / "BAT0" / "status").write_text("Discharging\n", encoding="utf-8")
    assert not power.on_ac and pol.paused_reason() == "PAUSED_ON_BATTERY"
    (tmp_path / "AC" / "online").write_text("1\n", encoding="utf-8")
    assert power.on_ac and pol.paused_reason() is None


@pytest.mark.unit
@pytest.mark.tc("DESK-023")
def test_desk_023_desktops_without_a_battery_count_as_mains(tmp_path):
    assert SysfsPower(tmp_path).on_ac  # no power_supply entries at all (desktop PC, VM, container)
    _supply(tmp_path, "ucsi-source-psy-1", "USB", online=0)  # an idle USB-C port is not "on battery"
    assert SysfsPower(tmp_path).on_ac
    _supply(tmp_path, "BAT1", "Battery", status="Discharging")
    assert not SysfsPower(tmp_path).on_ac
