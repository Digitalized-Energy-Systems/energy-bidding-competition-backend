"""The shape of the day (hackathon_backend/profiles.py, units/weather.py):

- the own load of every actor is always coverable without selling anything,
  and needs at most half of the battery
- all actors together can serve comfortably more than the demand at every
  step, also on the darkest day the weather allows; otherwise every order is
  awarded and the prices do not matter
- the morning and the evening are sparse, the midday is oversupplied

Checked with the perfect-foresight plan of the plotter (SystemView) on the
persisted units of a field of six actors.
"""

import json
import pytest
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.persistence import _as_state
from hackathon_backend.plot import SystemView
from hackathon_backend.units.weather import pv_irradiance_profile
from tests.helpers import COOPERATIVE_CONFIG, write_config

REQUIRED_MARGIN = 1.6
SEEDS = range(40)
N_ACTORS = 6


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _system(workdir, seed, **config):
    participants = [f"Team{i}xx" for i in range(N_ACTORS)]
    controller = Controller(
        write_config(workdir / "config.json", COOPERATIVE_CONFIG, participants=participants,
                     pv_seed=seed, **config),
        registration_keys_file=str(workdir / "keys.json"),
    )
    controller.general_demand = create_general_demand("gd0")
    for participant in participants:
        await controller.register_actor(participant)
    return SystemView(json.loads(_as_state(controller).model_dump_json()))


def _darkest_seed():
    return min(range(300), key=lambda seed: sum(pv_irradiance_profile(seed)))


def _block_margin(system, first_hour, last_hour):
    """How far the demand of these hours alone could rise, the rest at 1x."""
    def feasible(m):
        target = [
            d * (m if first_hour * 4 <= step < last_hour * 4 else 1.0)
            for step, d in enumerate(system.demand_kw)
        ]
        return all(r["not_servable_kw"] < 1e-6 and r["grid_kw"] < 1e-6 for r in system.serve(target))

    low, high = 1.0, 16.0
    for _ in range(25):
        middle = (low + high) / 2
        low, high = (middle, high) if feasible(middle) else (low, middle)
    return low


@pytest.mark.anyio
async def test_the_demand_is_comfortably_servable_on_every_day(workdir):
    margins = {}
    for seed in list(SEEDS) + [_darkest_seed()]:
        margins[seed] = (await _system(workdir, seed)).margin
    assert min(margins.values()) >= REQUIRED_MARGIN


@pytest.mark.anyio
async def test_the_own_load_is_always_coverable_and_needs_less_than_half_the_battery(workdir):
    for seed in list(SEEDS) + [_darkest_seed()]:
        system = await _system(workdir, seed)
        plan = system.serve([0.0] * system.steps)
        assert all(row["grid_kw"] < 1e-6 for row in plan)
        # energy the batteries deliver for the own load per deficit stretch
        stretch = worst = 0.0
        for net in system.surplus_kw:
            stretch = stretch + max(0.0, -net) * 0.25 if net < 0 else 0.0
            worst = max(worst, stretch)
        assert worst <= 0.5 * system.capacity_kwh


@pytest.mark.anyio
async def test_the_morning_and_evening_are_sparse_and_the_midday_oversupplied(workdir):
    systems = [await _system(workdir, seed) for seed in range(9)]
    median = sorted(systems, key=lambda system: system.margin)[len(systems) // 2]
    assert _block_margin(median, 6, 8) < 4.0
    assert _block_margin(median, 18, 22) < 3.0
    for first_hour in (10, 12, 14):
        assert _block_margin(median, first_hour, first_hour + 2) > 5.0


@pytest.mark.anyio
async def test_the_check_detects_a_day_the_field_cannot_serve(workdir):
    # a constant load of 1.2 kW leaves too little for the demand
    system = await _system(workdir, _darkest_seed(), actor_load_kw=1.2)
    assert system.margin < 1.0


@pytest.mark.anyio
async def test_actors_get_the_configured_load_and_battery_charge(workdir):
    controller = Controller(
        write_config(workdir / "config.json", actor_load_kw=0.7, battery_initial_soc_percent=40),
        registration_keys_file=str(workdir / "keys.json"),
    )
    controller.general_demand = create_general_demand("gd0")
    _, units = await controller.register_actor("TestA")
    by_kind = {
        ("soc" if "soc_percent" in u else "load" if "forecast_demand_p_kw" in u else "pv"): u
        for u in (unit.__dict__ for unit in units)
    }
    assert by_kind["soc"]["soc_percent"] == 40
    assert set(by_kind["load"]["forecast_demand_p_kw"]) == {0.7}


@pytest.mark.anyio
async def test_the_default_load_is_the_household_profile(workdir):
    system = await _system(workdir, 0)
    per_actor = [load / N_ACTORS for load in system.load_kw]
    assert max(per_actor) == pytest.approx(1.0)
    assert per_actor[7 * 4] == pytest.approx(1.0) and per_actor[19 * 4] == pytest.approx(1.0)
    assert min(per_actor) == pytest.approx(0.5)
