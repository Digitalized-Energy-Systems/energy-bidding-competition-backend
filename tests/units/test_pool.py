from hackathon_backend.units.pool import *


def test_pool_with_default_actors_neg_input():
    # GIVEN
    pool = UnitPool()
    uuid, root_node = allocate_default_actor_units()
    pool.insert_actor_root(uuid, root_node)
    input = UnitInput(delta_t=15 * 60, p_kw=-3, q_kvar=1)

    # WHEN
    output = pool.step_actor(uuid, input, 30)

    # THEN
    assert output.p_kw == -40.09720438561578


def test_pool_with_default_actors():
    # GIVEN
    pool = UnitPool()
    uuid, root_node = allocate_default_actor_units()
    pool.insert_actor_root(uuid, root_node)
    input = UnitInput(delta_t=15 * 60, p_kw=3, q_kvar=1)

    # WHEN
    output = pool.step_actor(uuid, input, 30)

    # THEN
    assert output.p_kw == -34.09720438561578
