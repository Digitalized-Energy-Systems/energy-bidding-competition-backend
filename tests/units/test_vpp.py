from hackathon_backend.units.vpp import *
from hackathon_backend.units.battery import create_battery
from hackathon_backend.units.pv import create_pv_unit
from hackathon_backend.units.load import create_demand


def test_vpp_battery_adjust_strategy_battery_cant_adjust():
    # GIVEN
    vpp = VPP(BatteryAdjustVPPStrategy())
    profile_day = [10 for _ in range(96)]
    battery = create_battery("b0")
    vpp.add_unit(create_demand("d0", profile_day, profile_day, 1))
    vpp.add_unit(create_pv_unit("pb0"))
    vpp.add_unit(battery)
    input = UnitInput(15 * 60, 20, 20)

    # WHEN
    result = vpp.step(input, 30)

    # THEN
    assert result.p_kw == 8.703380869292287


def test_vpp_battery_adjust_strategy_battery_can_adjust():
    # GIVEN
    vpp = VPP(BatteryAdjustVPPStrategy())
    profile_day = [5 for _ in range(96)]
    battery = create_battery(
        "b0", p_discharge_max_kw=10, p_charge_max_kw=10, cap_kwh=100
    )
    vpp.add_unit(create_demand("d0", profile_day, profile_day, 1))
    vpp.add_unit(create_pv_unit("pb0"))
    vpp.add_unit(battery)
    input = UnitInput(15 * 60, 5, 20)

    # WHEN
    result = vpp.step(input, 30)

    # THEN
    assert result.p_kw == 0
