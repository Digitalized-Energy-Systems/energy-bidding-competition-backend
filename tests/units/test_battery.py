from hackathon_backend.units.battery import *


def test_battery_step():
    # GIVEN
    battery_unit = MidasBatteryUnit(
        BatteryInformation(
            unit_id="b1",
            cap_kwh=1,
            p_charge_max_kw=1,
            p_discharge_max_kw=1,
            soc_percent=50,
        )
    )
    input = UnitInput(15 * 60, 1, 1)

    # WHEN
    result = battery_unit.step(input, 0)

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
        initial_soc=20,
    )

    # THEN the battery may always be discharged completely
    assert battery_unit.id == "b0"
    assert battery_unit._midas_battery.config.cap_kwh == 1.2
    assert battery_unit._midas_battery.config.p_charge_max_kw == 1
    assert battery_unit._midas_battery.config.p_discharge_max_kw == 1
    assert battery_unit._midas_battery.config.soc_min_percent == 0
    assert battery_unit._midas_battery.state.soc_percent == 20


def test_battery_is_restored_from_its_information():
    # GIVEN a battery which was discharged for one step
    battery_unit = create_battery("b0")
    battery_unit.step(UnitInput(15 * 60, -2, 0), 0)

    # WHEN it is rebuilt from its persisted information
    restored = MidasBatteryUnit(battery_unit.read_full_information())

    # THEN
    assert restored.id == "b0"
    assert restored.read_information() == battery_unit.read_information()
    assert restored._midas_battery.state.soc_percent < 50
