"""The central optimum (optimum.py) and the dispatch comparison of a run."""

import numpy as np
import pytest

from hackathon_backend.config import Config
from hackathon_backend.design_check import field
from hackathon_backend.general_demand import DEFAULT_LOAD_PROFILE
from hackathon_backend.optimum import central_optimum
from hackathon_backend.units.weather import pv_irradiance_profile
from tests.helpers import FAST_STEPPING, client, wait_until

STEPS = 96
START_KWH = 12 * Config.model_fields["battery_initial_soc_percent"].default / 100


def _teams(n, pv, load, steps=STEPS):
    return dict(
        pv_kw=np.tile(pv, (n, 1))[:, :steps],
        load_kw=np.tile(load, (n, 1))[:, :steps],
        active=np.ones((n, steps), dtype=bool),
        cap_kwh=np.full(n, 12.0),
        charge_max_kw=np.full(n, 2.0),
        discharge_max_kw=np.full(n, 2.0),
        initial_kwh=np.full(n, START_KWH),
    )


def _balance(side, start_kwh, pv_kwh, load_kwh):
    """start + PV - own loads + grid - served - lost - left at the end"""
    kwh = lambda name: float(np.sum(side[name])) * 0.25  # noqa: E731
    return (start_kwh + pv_kwh - load_kwh + kwh("grid_kw") - kwh("served_kw") - kwh("lost_kw")
            - float(side["stored_kwh"][-1]))


def test_the_optimum_serves_the_day_and_loses_as_little_as_the_perfect_foresight_plan():
    # GIVEN six default teams on a weather day and their market demand
    system = field(6, pv_irradiance_profile(41))
    demand = np.array(system.demand_kw)
    teams = _teams(6, np.array(system.pv_kw) / 6, np.array(system.load_kw) / 6)

    # WHEN
    result = central_optimum(**teams, demand_kw=demand)

    # THEN everything is served without grid draw; with lossless batteries
    # the greedy plan of the plotter keeps as much energy, so both lose the
    # same, and the energy balance closes
    assert result["served_kw"] == pytest.approx(demand, abs=1e-6)
    assert result["unserved_kw"].sum() == pytest.approx(0, abs=1e-6)
    assert result["grid_kw"].sum() == pytest.approx(0, abs=1e-6)
    greedy = system.plan
    assert result["stored_kwh"][-1] == pytest.approx(greedy[-1]["stored_kwh"], abs=1e-4)
    assert min(result["stored_kwh"]) == pytest.approx(min(r["stored_kwh"] for r in greedy), abs=1e-4)
    assert result["lost_kw"].sum() > 0
    assert _balance(result, 6 * START_KWH, sum(system.pv_kw) * 0.25, sum(system.load_kw) * 0.25) == pytest.approx(
        0, abs=1e-6
    )


def test_demand_beyond_the_units_is_unserved_and_the_grid_only_covers_the_own_load():
    # GIVEN one team without PV and an empty battery, asked for 3 kW
    teams = _teams(1, np.zeros(4), np.full(4, 0.5), steps=4)
    teams["initial_kwh"] = np.array([0.0])

    # WHEN
    result = central_optimum(**teams, demand_kw=np.full(4, 3.0))

    # THEN nothing is served, its own load comes from the grid, and the
    # grid does not charge the battery
    assert result["unserved_kw"] == pytest.approx([3.0] * 4)
    assert result["grid_kw"] == pytest.approx([0.5] * 4)
    assert result["stored_kwh"] == pytest.approx([0.0] * 4)


def test_an_inactive_team_does_not_deliver():
    # GIVEN two teams with empty batteries, the second not registered yet
    teams = _teams(2, np.full(3, 3.0), np.zeros(3), steps=3)
    teams["initial_kwh"] = np.zeros(2)
    teams["active"][1] = False

    result = central_optimum(**teams, demand_kw=np.full(3, 4.0))

    # THEN only the first team's 3 kW of PV per step are served
    assert result["served_kw"].sum() == pytest.approx(9.0)
    assert result["unserved_kw"].sum() == pytest.approx(3.0)


def test_surplus_beyond_the_battery_is_lost():
    # GIVEN a full battery, 3 kW PV and 1 kW demand
    teams = _teams(1, np.full(2, 3.0), np.zeros(2), steps=2)
    teams["initial_kwh"] = np.array([12.0])

    result = central_optimum(**teams, demand_kw=np.full(2, 1.0))

    assert result["lost_kw"] == pytest.approx([2.0, 2.0])


@pytest.mark.anyio
async def test_status_and_dispatch_after_the_last_step(api):
    # GIVEN a short day of two teams
    app, controller = api(participants=["TestA", "TestB", "TestC"], max_steps=8, **FAST_STEPPING)
    for participant in ("TestA", "TestB"):
        await controller.register_actor(participant)
    async with client(app) as ac:
        assert (await ac.get("/ui/status")).json() == {"step": 0, "max_steps": 8, "finished": False}
        assert (await ac.get("/ui/dispatch")).json()["steps"] == 0

        # WHEN the day is over
        controller.init()
        await wait_until(lambda: controller.step == 8)
        status = (await ac.get("/ui/status")).json()
        dispatch = (await ac.get("/ui/dispatch")).json()

    # THEN the actual dispatch stands next to the optimum of the same steps
    assert status == {"step": 8, "max_steps": 8, "finished": True}
    assert dispatch["finished"] and dispatch["steps"] == 8 and dispatch["teams"] == 2
    tenders = [round(2 * float(d), 1) for d in DEFAULT_LOAD_PROFILE[5:8]]
    assert dispatch["demand_kw"] == pytest.approx([0.0] * 5 + tenders)
    # nobody sold anything, the optimum serves it all
    assert dispatch["actual"]["unserved_kwh"] == pytest.approx(dispatch["demand_kwh"])
    assert dispatch["optimum"]["served_kwh"] == pytest.approx(dispatch["demand_kwh"])
    # AND the energy balance closes on both sides
    for side in ("actual", "optimum"):
        assert len(dispatch[side]["stored_kwh"]) == 8
        assert _balance(
            dispatch[side], dispatch["initial_kwh"], dispatch["pv_kwh"], dispatch["load_kwh"]
        ) == pytest.approx(0, abs=1e-2)
