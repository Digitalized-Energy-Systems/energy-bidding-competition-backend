import pytest
from hackathon_backend.units.pool import *


def _default_actor():
    pool = UnitPool()
    uuid, root_node = allocate_default_actor_units(pv_profile=clear_sky_profile())
    pool.insert_actor_root(uuid, root_node)
    return pool, uuid, root_node


def _net_generation_kw(root_node, step):
    pv_kw = -root_node.sub_units["pb0"].get_pv_power(UnitInput(15 * 60, 0, 0), step).p_kw
    load_kw = root_node.sub_units["d0"].step(UnitInput(15 * 60, 0, 0), step).p_kw
    return pv_kw - load_kw


def test_pool_with_default_actors_neg_input():
    # GIVEN
    pool, uuid, root_node = _default_actor()
    battery = root_node.sub_units["b0"].read_information()
    input = UnitInput(delta_t=15 * 60, p_kw=-3, q_kvar=1)

    # WHEN
    output = pool.step_actor(uuid, input, 30)

    # THEN the battery charges at its maximum, the rest is drawn from the grid
    assert output.p_kw == pytest.approx(
        _net_generation_kw(root_node, 30) - battery.p_charge_max_kw
    )
    assert output.p_kw > -3


def test_pool_with_default_actors():
    # GIVEN
    pool, uuid, root_node = _default_actor()
    battery = root_node.sub_units["b0"].read_information()
    input = UnitInput(delta_t=15 * 60, p_kw=3, q_kvar=1)

    # WHEN
    output = pool.step_actor(uuid, input, 30)

    # THEN the battery discharges at its maximum, which is not enough for 3 kW
    assert output.p_kw == pytest.approx(
        _net_generation_kw(root_node, 30) + battery.p_discharge_max_kw
    )
    assert output.p_kw < 3


def test_pool_with_default_actors_delivers_feasible_setpoint():
    # GIVEN
    pool, uuid, _ = _default_actor()

    # WHEN
    output = pool.step_actor(uuid, UnitInput(delta_t=15 * 60, p_kw=1, q_kvar=0), 30)

    # THEN
    assert output.p_kw == pytest.approx(1)
    assert [ui.unit_id for ui in pool.read_units(uuid)] == ["d0", "pb0", "b0"]
