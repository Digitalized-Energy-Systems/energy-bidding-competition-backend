"""Tender and minimum order amount."""

import pytest
from hackathon_backend.market.tender import (
    COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW,
    cooperative_minimum_order_amount_kw,
    plain_minimum_order_amount_kw,
    supply_step_from_time,
)
from hackathon_backend.general_demand import DEFAULT_LOAD_PROFILE
from hackathon_backend.units.pool import allocate_default_actor_units
from hackathon_backend.units.unit import UnitInput
from hackathon_backend.units.weather import clear_sky_profile, pv_irradiance_profile

STEPS = 96


def _tender_kw(supply_step, actors):
    # the tender is the demand: registered actors times the demand per actor
    # of the supply step, rounded to 0.1 kW like the controller
    return round(actors * float(DEFAULT_LOAD_PROFILE[supply_step]), 1)


def _default_actor_delivers_kw(step, p_kw, pv_profile=None):
    """Power a fresh default actor (battery at 65 %) delivers in the step
    when asked for p_kw (clear sky without fog by default)."""
    _, root = allocate_default_actor_units(pv_profile=pv_profile or clear_sky_profile())
    return root.step(UnitInput(delta_t=900, p_kw=p_kw, q_kvar=0), step, other_inputs=[]).p_kw


def test_cooperative_minimum_is_1_8_kw_but_never_more_than_the_tender():
    assert COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW == 1.8
    assert cooperative_minimum_order_amount_kw(5.0) == pytest.approx(1.8)
    assert cooperative_minimum_order_amount_kw(1.8) == pytest.approx(1.8)
    assert cooperative_minimum_order_amount_kw(1.2) == pytest.approx(1.2)
    assert cooperative_minimum_order_amount_kw(0.0) == pytest.approx(0.1)


def test_the_plain_mode_has_no_minimum_beyond_the_resolution_of_the_tender():
    assert plain_minimum_order_amount_kw(4.0) == pytest.approx(0.1)
    assert plain_minimum_order_amount_kw(0.1) == pytest.approx(0.1)
    # a configured minimum is capped at the tender like the cooperative one
    assert plain_minimum_order_amount_kw(4.0, minimum_kw=1.0) == pytest.approx(1.0)
    assert plain_minimum_order_amount_kw(0.6, minimum_kw=1.0) == pytest.approx(0.6)


@pytest.mark.parametrize("actors", range(6, 16))
def test_a_minimum_sized_order_fits_every_auction_from_6_to_15_actors(actors):
    for supply_step in range(STEPS):
        tender = _tender_kw(supply_step, actors)
        assert tender >= COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW
        assert cooperative_minimum_order_amount_kw(tender) == pytest.approx(1.8)


def test_smaller_fields_still_get_an_order_in():
    for supply_step in range(STEPS):
        tender = _tender_kw(supply_step, 3)
        assert cooperative_minimum_order_amount_kw(tender) <= tender


def test_one_actor_needs_a_cooperative_bid_at_night_and_sells_alone_by_day():
    # the docs (tender.py, README) promise the minimum to be reachable by a
    # single default actor on a clear day from 03:00 to 19:30, i.e. steps
    # 12..77, and never in the rest of the night
    reachable = {
        step for step in range(STEPS)
        if _default_actor_delivers_kw(step, 1.8) >= 1.8 - 1e-9
    }
    assert reachable == set(range(12, 78))
    for step in set(range(STEPS)) - reachable:
        # two actors deliver the minimum with slack
        assert 2 * _default_actor_delivers_kw(step, 5.0) > 1.5 * 1.8


def test_with_the_weather_of_a_run_the_night_stays_cooperative():
    for seed in range(5):
        profile = pv_irradiance_profile(seed)
        for step in list(range(0, 12)) + list(range(80, STEPS)):
            assert _default_actor_delivers_kw(step, 1.8, profile) < 1.8


def test_supply_step_from_time():
    assert supply_step_from_time(0) == 0
    assert supply_step_from_time(5400) == 6
    assert supply_step_from_time(5400.0) == 6
    assert supply_step_from_time(43200) == 48
    assert supply_step_from_time(86400) == 0
    assert supply_step_from_time(86400 + 900) == 1
