"""Accounting of awarded orders per actor.

A group order (an order with several agents) is credited to every member's
OWN account by its awarded share, i.e. the value of the order is split by the
power amount each member put into it. There is no joint account. Run from the
backend repository root. The actors get a clear-sky day, so their delivery
at noon does not depend on the weather of the run.
"""

import pytest
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.accounting.account import AWARDED_AMOUNT, PAYOFF, PROVIDED_POWER
from hackathon_backend.accounting.accounter import PENALTY_CT_PER_KW
from hackathon_backend.config import GRID_PENALTY_CT_PER_KW
from hackathon_backend.market.auction import (
    AuctionParameters,
    AuctionResult,
    AwardedOrder,
)
from hackathon_backend.units.weather import clear_sky_profile
from tests.helpers import PLAIN_CONFIG

# supply time 43200 s is step 48 (noon), where each actor can supply ~4 kW
NOON_SUPPLY_TIME = 43200
MIDNIGHT_SUPPLY_TIME = 0


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _params(supply_start_time):
    return AuctionParameters(
        product_type="electricity",
        gate_opening_time=supply_start_time - 4500,
        gate_closure_time=supply_start_time - 900,
        supply_start_time=supply_start_time,
        supply_duration_s=900,
        tender_amount_kw=4.0,
        minimum_order_amount_kw=2.0,
    )


async def _controller_with_actors(*participant_ids, **config):
    controller = Controller(str(PLAIN_CONFIG))
    controller.config = controller.config.model_copy(update=config)
    controller.general_demand = create_general_demand("gd0")
    controller.pv_profile = clear_sky_profile()
    actor_ids = []
    for participant_id in participant_ids:
        actor_id, _ = await controller.register_actor(participant_id)
        actor_ids.append(actor_id)
    return controller, actor_ids


def _inject_result(controller, awarded_orders, supply_start_time=NOON_SUPPLY_TIME):
    controller.market.current_auction_results = [
        AuctionResult(
            auction_id="x",
            params=_params(supply_start_time),
            clearing_price=10,
            awarded_orders=awarded_orders,
        )
    ]


def _empty_battery(controller, actor_id):
    battery = controller.unit_pool.actor_to_root[actor_id].sub_units["b0"]
    battery._midas_battery.state.soc_percent = 0


@pytest.mark.anyio
async def test_group_order_is_credited_per_member_by_amount():
    # GIVEN two actors and an awarded group order [1, 3] kW at 10 ct
    controller, (a, b) = await _controller_with_actors("TestA", "TestB")
    _inject_result(
        controller,
        [
            AwardedOrder(
                auction_id="x",
                agents=[a, b],
                amount_kw=[1.0, 3.0],
                awarded_amount_kw=[1.0, 3.0],
                price_ct=10,
            )
        ],
    )

    # WHEN the units deliver at noon
    controller.step_units(current_time=NOON_SUPPLY_TIME)

    # THEN only the accounts of A and B exist (no joint key) and the value
    # of the order is split by the amount each member put into the bid
    assert set(controller.actor_accounts.keys()) == {a, b}
    assert controller.actor_accounts[a].get_balance() == pytest.approx(10, abs=1e-6)
    assert controller.actor_accounts[b].get_balance() == pytest.approx(30, abs=1e-6)
    assert set(controller.get_balance_dict_sync().keys()) == {a, b}
    # the awarded amount recorded in the transaction is the member's own share
    assert controller.actor_accounts[a].transactions[AWARDED_AMOUNT].iloc[-1] == 1.0
    assert controller.actor_accounts[b].transactions[AWARDED_AMOUNT].iloc[-1] == 3.0


@pytest.mark.anyio
async def test_single_agent_order_keeps_one_account():
    # GIVEN one actor and an awarded single-agent order of 2 kW at 10 ct
    controller, (a,) = await _controller_with_actors("TestA")
    _inject_result(
        controller,
        [
            AwardedOrder(
                auction_id="x",
                agents=[a],
                amount_kw=[2.0],
                awarded_amount_kw=[2.0],
                price_ct=10,
            )
        ],
    )

    # WHEN
    controller.step_units(current_time=NOON_SUPPLY_TIME)

    # THEN there is exactly one account with the full payoff of the order
    assert set(controller.actor_accounts.keys()) == {a}
    assert controller.actor_accounts[a].get_balance() == pytest.approx(20, abs=1e-6)
    assert len(controller.actor_accounts[a].transactions) == 1
    assert controller.actor_accounts[a].transactions[AWARDED_AMOUNT].iloc[-1] == 2.0


@pytest.mark.anyio
async def test_no_awarded_order_penalty_lands_on_own_account():
    # GIVEN one actor without any awarded order at midnight
    controller, (a,) = await _controller_with_actors("TestA")
    controller.market.current_auction_results = []

    # WHEN the units are stepped with setpoint 0 (must never raise)
    controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)

    # THEN whatever the sign of the delivered power, everything is booked on
    # the actor's own account and nothing was earned
    assert set(controller.actor_accounts.keys()) == {a}
    transactions = controller.actor_accounts[a].transactions
    # without an awarded order there is no payoff row, only a possible penalty
    assert (transactions[AWARDED_AMOUNT] == 0).all()
    assert controller.actor_accounts[a].get_balance() <= 0
    assert (transactions[PAYOFF] <= 0).all()


@pytest.mark.anyio
async def test_penalty_after_drained_battery_lands_on_own_account():
    # GIVEN one actor without awarded orders and a constant load, whose
    # battery is drained by it over the midnight steps
    controller, (a,) = await _controller_with_actors("TestA", actor_load_kw=0.8)
    load_kw = controller.config.actor_load_kw
    stored_kwh = 12 * controller.config.battery_initial_soc_percent / 100
    controller.market.current_auction_results = []
    for _ in range(round(stored_kwh / (load_kw * 0.25))):
        controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)
    assert controller.actor_accounts[a].get_balance() == pytest.approx(0, abs=1e-6)

    # WHEN the load can no longer be covered
    controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)

    # THEN the grid penalty (the load times 1500 ct by default) is booked on
    # the actor's own account
    assert controller.config.grid_penalty_ct_per_kw == GRID_PENALTY_CT_PER_KW
    assert set(controller.actor_accounts.keys()) == {a}
    assert controller.actor_accounts[a].get_balance() == pytest.approx(
        -load_kw * GRID_PENALTY_CT_PER_KW, abs=1e-6
    )
    penalty_row = controller.actor_accounts[a].transactions.iloc[-1]
    assert penalty_row[AWARDED_AMOUNT] == 0
    assert penalty_row[PAYOFF] == pytest.approx(-load_kw * GRID_PENALTY_CT_PER_KW, abs=1e-6)


@pytest.mark.anyio
@pytest.mark.parametrize("grid_penalty_ct_per_kw", [1000, 250, 0])
async def test_grid_penalty_is_charged_per_kw_of_net_grid_draw(grid_penalty_ct_per_kw):
    # GIVEN an actor with an empty battery at midnight (no PV) and another one
    # whose battery covers its load
    controller, (a, b) = await _controller_with_actors(
        "TestA", "TestB", grid_penalty_ct_per_kw=grid_penalty_ct_per_kw, actor_load_kw=0.8
    )
    _empty_battery(controller, a)
    controller.market.current_auction_results = []

    # WHEN
    controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)

    # THEN A draws its load from the grid and pays the configured rate
    load_kw = controller.config.actor_load_kw
    provided = controller.actor_accounts[a].transactions.iloc[-1]
    assert provided[PROVIDED_POWER] == pytest.approx(-load_kw)
    assert controller.actor_accounts[a].get_balance() == pytest.approx(
        -load_kw * grid_penalty_ct_per_kw
    )
    assert controller.actor_accounts[b].get_balance() == 0
    assert controller.actor_accounts[b].transactions.empty


@pytest.mark.anyio
async def test_grid_penalty_is_charged_on_top_of_the_shortfall():
    # GIVEN an actor with a constant load, an empty battery and an awarded
    # 1 kW order at midnight
    controller, (a,) = await _controller_with_actors("TestA", actor_load_kw=0.8)
    _empty_battery(controller, a)
    _inject_result(
        controller,
        [
            AwardedOrder(
                auction_id="x",
                agents=[a],
                amount_kw=[1.0],
                awarded_amount_kw=[1.0],
                price_ct=10,
            )
        ],
        supply_start_time=MIDNIGHT_SUPPLY_TIME,
    )

    # WHEN
    controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)

    # THEN the missing kW costs the shortfall penalty and the kW drawn for
    # the own load the grid penalty
    load_kw = controller.config.actor_load_kw
    payoffs = list(controller.actor_accounts[a].transactions[PAYOFF])
    assert payoffs == pytest.approx([-1 * PENALTY_CT_PER_KW, -load_kw * GRID_PENALTY_CT_PER_KW])
    assert controller.actor_accounts[a].get_balance() == pytest.approx(
        -(PENALTY_CT_PER_KW + load_kw * GRID_PENALTY_CT_PER_KW)
    )
