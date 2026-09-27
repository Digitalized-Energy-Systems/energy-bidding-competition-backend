"""Tender and minimum order amount profiles of cooperative bidding."""

import math
import pytest
from hackathon_backend.market.tender import (
    supply_step_from_time,
    cooperative_minimum_order_amount_kw,
    cooperative_tender_amount_kw,
    cooperative_tender_floor_kw,
)
from hackathon_backend.general_demand import DEFAULT_LOAD_PROFILE
from hackathon_backend.units.pool import allocate_default_actor_units
from hackathon_backend.units.unit import UnitInput

STEPS = 96


def _single_actor_capacity_kw(step):
    # 1 kW load, 3 kW PV peak on a cosine irradiance and a 2 kW battery
    return 2.5 - 1.5 * math.cos(2 * math.pi * step / STEPS)


def _plain_tender_kw(step, participants):
    # tender of the plain mode: registered actors times the general demand
    # per actor (rounded to 0.1 kW like GeneralDemand does)
    return round(participants * round(float(DEFAULT_LOAD_PROFILE[step]), 1), 1)


def test_minimum_profile():
    reachable_alone = 0
    for step in range(STEPS):
        minimum = cooperative_minimum_order_amount_kw(step)
        assert 2.0 <= minimum <= 3.0
        if minimum <= _single_actor_capacity_kw(step) + 1e-9:
            reachable_alone += 1
    # roughly half of the day the minimum is reachable by a single actor
    assert 44 <= reachable_alone <= 52


SHARE = 0.5


def test_tender_scales_with_participants_like_plain_mode():
    for participants in (1, 2, 3, 5, 8, 10, 11, 15, 16, 20, 40):
        floor_active = 0
        for creation_step in range(STEPS):
            supply_step = (creation_step + 5) % STEPS
            plain = _plain_tender_kw(creation_step, participants)
            tender = cooperative_tender_amount_kw(supply_step, plain, SHARE)
            minimum = cooperative_minimum_order_amount_kw(supply_step)
            # never below the plain tender, and the minimum is never more
            # than the share of the tender (two minimum-sized bids fit)
            assert tender >= plain - 1e-9
            assert minimum <= SHARE * tender + 1e-9
            floor = cooperative_tender_floor_kw(supply_step, SHARE)
            assert floor == pytest.approx(2 * minimum)
            if plain >= floor:
                assert tender == pytest.approx(plain)
            else:
                assert tender == pytest.approx(floor)
                floor_active += 1
        # up to 10 actors the floor is active all day, from 16 actors on the
        # tender is identical to the plain mode
        if participants <= 10:
            assert floor_active == STEPS
        elif participants >= 16:
            assert floor_active == 0
        else:
            assert 0 < floor_active < STEPS
    # the number of minimum-sized bids which fit grows with the field
    bids_10 = cooperative_tender_amount_kw(0, _plain_tender_kw(91, 10), SHARE) / 2.0
    bids_20 = cooperative_tender_amount_kw(0, _plain_tender_kw(91, 20), SHARE) / 2.0
    assert bids_10 == pytest.approx(2.0)
    assert bids_20 == pytest.approx(3.0)


def test_floor_keeps_minimum_within_share_for_any_share():
    for share in (0.3, 0.5, 0.6, 0.75, 1.0):
        for step in range(STEPS):
            minimum = cooperative_minimum_order_amount_kw(step)
            floor = cooperative_tender_floor_kw(step, share)
            assert minimum <= share * floor + 1e-9
            # and not more than 0.1 kW above the exact value
            assert floor - minimum / share < 0.1 + 1e-9
            # share 1 floors the tender at the minimum itself
            if share == 1.0:
                assert floor == pytest.approx(minimum)


def _fresh_default_actor_delivers_kw(step, p_kw):
    """Power a fresh default actor (battery at 50 %) delivers in the step
    when asked for p_kw."""
    _, root = allocate_default_actor_units()
    return root.step(UnitInput(delta_t=900, p_kw=p_kw, q_kvar=0), step, other_inputs=[]).p_kw


def test_minimum_reachable_alone_by_default_units_matches_docs():
    # the docs (tender.py, README) promise the minimum to be reachable by a
    # single default actor from about 06:45 to 17:15, i.e. steps 27..69;
    # the analytic capacity formula overestimates the units near 06:00
    # and 18:00 by about 6 %
    reachable = set()
    for step in range(STEPS):
        minimum = cooperative_minimum_order_amount_kw(step)
        if _fresh_default_actor_delivers_kw(step, minimum) >= minimum - 1e-9:
            reachable.add(step)
    assert reachable == set(range(27, 70))
    # the boundary steps of the analytic window under-deliver slightly
    for step in (24, 72):
        minimum = cooperative_minimum_order_amount_kw(step)
        delivered = _fresh_default_actor_delivers_kw(step, minimum)
        assert 0.9 * minimum < delivered < minimum


def test_profile_extremes():
    assert cooperative_minimum_order_amount_kw(0) == pytest.approx(2.0)
    assert cooperative_minimum_order_amount_kw(48) == pytest.approx(3.0)
    # floor at minimum / share for a small field, plain tender for a large one
    assert cooperative_tender_amount_kw(0, 0.9, 0.5) == pytest.approx(4.0)
    assert cooperative_tender_amount_kw(48, 1.0, 0.5) == pytest.approx(6.0)
    assert cooperative_tender_amount_kw(0, 0.9, 1.0) == pytest.approx(2.0)
    assert cooperative_tender_amount_kw(0, 7.0, 0.5) == pytest.approx(7.0)
    assert cooperative_tender_amount_kw(48, 10.0, 0.5) == pytest.approx(10.0)


def test_supply_step_from_time():
    assert supply_step_from_time(0) == 0
    assert supply_step_from_time(5400) == 6
    assert supply_step_from_time(5400.0) == 6
    assert supply_step_from_time(43200) == 48
    assert supply_step_from_time(86400) == 0
    assert supply_step_from_time(86400 + 900) == 1
