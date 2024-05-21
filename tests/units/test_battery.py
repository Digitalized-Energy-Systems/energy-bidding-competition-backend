from hackathon_backend.units.battery import *


def test_battery_step():
    # GIVEN
    battery_unit = MidasBatteryUnit(
        "b1",
        Battery(
            {
                "cap_kwh": 1,
                "p_charge_max_kw": 1,
                "p_discharge_max_kw": 1,
                "soc_min_percent": 0,
                "eta_pc": [0, 0, 100],
            },
            {"soc_percent": 50},
        ),
    )
    input = UnitInput(15 * 60, 1, 1)

    # WHEN
    result = battery_unit.step(input)

    # THEN
    assert result.p_kw == 1
    assert result.q_kvar == 0
    assert battery_unit._midas_battery.state.soc_percent == 75


def test_create_battery():
    # GIVEN
    # WHEN
    battery_unit = create_battery(
        "b0",
        cap_kwh=1.2,
        p_charge_max_kw=1,
        p_discharge_max_kw=1,
        soc_min_percent=1,
        initial_soc=20,
    )

    # THEN
    assert battery_unit.id == "b0"
    assert battery_unit._midas_battery.config.cap_kwh == 1.2
    assert battery_unit._midas_battery.config.p_charge_max_kw == 1
    assert battery_unit._midas_battery.config.p_discharge_max_kw == 1
    assert battery_unit._midas_battery.config.soc_min_percent == 1
    assert battery_unit._midas_battery.state.soc_percent == 20
