from hackathon_backend.units.load import *


def test_load_step():
    # GIVEN
    demand_unit = SimpleDemandUnit(
        "d1",
        SimpleDemand([1, 2, 3], [0, 1, 0], 1),
    )

    # WHEN
    result = demand_unit.step(None, 0, None)

    # THEN
    assert result.p_kw == 1
    assert result.q_kvar == 0

    # WHEN
    result = demand_unit.step(None, 1, None)

    # THEN
    assert result.p_kw == 2
    assert result.q_kvar == 1

    # WHEN
    result = demand_unit.step(None, 2, None)

    # THEN
    assert result.p_kw == 3
    assert result.q_kvar == 0


def test_create_demand():
    # GIVEN
    # WHEN
    demand_unit = create_demand("d0", [1], [2], 1)

    # THEN
    assert demand_unit.id == "d0"
    assert demand_unit._simple_demand._perfect_demand_p_kw[0] == 1
    assert demand_unit._simple_demand._perfect_demand_p_kvar[0] == 2
    assert demand_unit._simple_demand._uncertainty == 1


def test_get_forecast():
    # GIVEN
    demand_unit = SimpleDemandUnit(
        "d1",
        SimpleDemand([1, 2, 3], [0, 1, 0], 1),
    )

    # WHEN
    forecast = demand_unit.get_forecast(0, 3)

    # THEN
    assert forecast == ([1, 2, 3], [0, 1, 0])
