"""The constants of the day and of the market live in the config."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hackathon_backend.accounting.accounter import PENALTY_CT_PER_KW, ElectricityAskAuctionAccounter
from hackathon_backend.config import Config, load_config
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand, demand_per_actor_profile
from hackathon_backend.market.auction import initiate_electricity_ask_auction
from hackathon_backend.profiles import DEMAND_PER_ACTOR_KW, HOUSEHOLD_LOAD_KW
from tests.helpers import COOPERATIVE_CONFIG, PLAIN_CONFIG, write_config

REPOSITORY_CONFIG = Path(__file__).parents[1] / "config.json"
# the defaults (the values these settings had as constants in the code,
# except the plain minimum order, which is only the resolution of the tender)
DEFAULTS = {
    "household_load_kw": HOUSEHOLD_LOAD_KW,
    "demand_per_actor_kw": DEMAND_PER_ACTOR_KW,
    "pv_peak_kw": 3.0,
    "battery_capacity_kwh": 12,
    "battery_charge_max_kw": 2,
    "battery_discharge_max_kw": 2,
    "battery_initial_soc_percent": 65,
    "maximum_price_ct": 1000,
    "shortfall_penalty_ct_per_kw": 250,
    "grid_penalty_ct_per_kw": 1500,
    "cooperative_minimum_order_kw": 1.8,
    "plain_minimum_order_kw": 0.1,
}


@pytest.mark.parametrize("config_file", [PLAIN_CONFIG, REPOSITORY_CONFIG], ids=["defaults", "config.json"])
def test_the_repository_config_has_the_defaults(config_file):
    config = load_config(config_file)
    for name, value in DEFAULTS.items():
        assert getattr(config, name) == pytest.approx(value), name


def test_the_repository_config_lists_every_constant():
    assert DEFAULTS.keys() <= json.loads(REPOSITORY_CONFIG.read_text()).keys()


def test_the_demand_per_actor_is_that_of_the_general_demand_unit():
    unit = create_general_demand("gd0")
    assert demand_per_actor_profile() == [unit.step(None, step).p_kw for step in range(96)]


def test_hourly_profiles_need_one_non_negative_value_per_hour():
    base = json.loads(PLAIN_CONFIG.read_text())
    for profile in ([0.5] * 23, [0.5] * 25, [-0.1] + [0.5] * 23):
        with pytest.raises(ValidationError):
            Config.model_validate({**base, "household_load_kw": profile})
        with pytest.raises(ValidationError):
            Config.model_validate({**base, "demand_per_actor_kw": profile})


@pytest.mark.anyio
async def test_configured_units_and_market_rules_apply(workdir):
    # GIVEN a config which changes every constant of the units and the market
    config_file = write_config(
        workdir / "config.json",
        COOPERATIVE_CONFIG,
        household_load_kw=[0.3] * 24,
        demand_per_actor_kw=[0.5] * 24,
        pv_peak_kw=4.0,
        battery_capacity_kwh=10,
        battery_charge_max_kw=1.5,
        battery_discharge_max_kw=2.5,
        maximum_price_ct=800,
        cooperative_minimum_order_kw=1.2,
    )
    controller = Controller(config_file, registration_keys_file=str(workdir / "keys.json"))
    controller.general_demand = create_general_demand("gd0")

    # WHEN three actors register and an auction opens
    actor_ids = [(await controller.register_actor(p))[0] for p in ("TestA", "TestB", "TestC")]
    controller.step_market(current_time=0)

    # THEN the units are built from the config
    units = controller.unit_pool.actor_to_root[actor_ids[0]].read_full_information().unit_information_list
    battery = next(u for u in units if hasattr(u, "cap_kwh"))
    pv = next(u for u in units if hasattr(u, "a_m2"))
    load = next(u for u in units if hasattr(u, "perfect_demand_p_kw"))
    assert (battery.cap_kwh, battery.p_charge_max_kw, battery.p_discharge_max_kw) == (10, 1.5, 2.5)
    assert pv.a_m2 == pytest.approx(16.0)
    assert set(load.perfect_demand_p_kw) == {0.3}
    # AND so is the auction: three actors times 0.5 kW
    params = controller.market.open_auctions[-1].params
    assert params.tender_amount_kw == pytest.approx(1.5)
    assert params.minimum_order_amount_kw == pytest.approx(1.2)
    assert params.maximum_price_ct == 800


def test_the_shortfall_penalty_is_configurable():
    # GIVEN 1 kW awarded at 10 ct and nothing delivered
    auction = initiate_electricity_ask_auction(0, tender_amount=2.0, minimum_order_amount_kw=0.1)
    auction.place_order(amount_kw=[1.0], price_ct=10, agents=["a"])
    result = auction.clear()

    # THEN the shortfall costs at least the penalty rate
    assert ElectricityAskAuctionAccounter(result).calculate_payoff("a", 0) == pytest.approx(-PENALTY_CT_PER_KW)
    assert ElectricityAskAuctionAccounter(result, shortfall_penalty_ct_per_kw=400).calculate_payoff(
        "a", 0
    ) == pytest.approx(-400)
