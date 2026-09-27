import pytest
from hackathon_backend.units.vpp import *
from hackathon_backend.units.battery import create_battery
from hackathon_backend.units.pv import create_pv_unit
from hackathon_backend.units.load import create_demand


def _pv_generation_kw(step):
    return -create_pv_unit("pv").step(UnitInput(15 * 60, 0, 0), step).p_kw


def test_vpp_battery_adjust_strategy_battery_cant_adjust():
    # GIVEN a 40 kW setpoint far beyond PV plus the 2 kW battery
    vpp = VPP(BatteryAdjustVPPStrategy())
    profile_day = [10 for _ in range(96)]
    battery = create_battery("b0")
    vpp.add_unit(create_demand("d0", profile_day, profile_day, 1))
    vpp.add_unit(create_pv_unit("pb0"))
    vpp.add_unit(battery)
    input = UnitInput(15 * 60, 40, 40)

    # WHEN
    result = vpp.step(input, 30)

    # THEN the battery discharges at its maximum and the VPP delivers what
    # PV, load and battery allow
    assert result.p_kw == pytest.approx(_pv_generation_kw(30) - 10 + 2)
    assert result.p_kw < 40
    assert battery._midas_battery.state.soc_percent == pytest.approx(
        50 - 100 * 2 * 0.25 / 12
    )


def test_vpp_battery_adjust_strategy_battery_can_adjust():
    # GIVEN a setpoint the 10 kW battery can balance
    vpp = VPP(BatteryAdjustVPPStrategy())
    profile_day = [5 for _ in range(96)]
    battery = create_battery(
        "b0", p_discharge_max_kw=10, p_charge_max_kw=10, cap_kwh=100
    )
    vpp.add_unit(create_demand("d0", profile_day, profile_day, 1))
    vpp.add_unit(create_pv_unit("pb0"))
    vpp.add_unit(battery)
    input = UnitInput(15 * 60, 4, 20)

    # WHEN
    result = vpp.step(input, 30)

    # THEN the VPP delivers exactly the setpoint and the battery covers the
    # difference between setpoint plus load and PV
    assert result.p_kw == pytest.approx(4)
    discharge_kw = 4 + 5 - _pv_generation_kw(30)
    assert 0 < discharge_kw < 10
    assert battery._midas_battery.state.soc_percent == pytest.approx(
        50 - 100 * discharge_kw * 0.25 / 100
    )
