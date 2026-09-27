"""Accounting of awarded orders per actor.

A group order (an order with several agents) is credited to every member's
OWN account by its awarded share, i.e. the value of the order is split by the
power amount each member put into it. There is no joint account. Run from the
backend repository root (Controller() reads config.json from the cwd).
"""

import pytest
from hackathon_backend.controller import Controller
from hackathon_backend.config import load_config
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.accounting.account import AWARDED_AMOUNT, PAYOFF
from hackathon_backend.market.auction import (
    AuctionParameters,
    AuctionResult,
    AwardedOrder,
)

# supply time 43200 s is step 48 (noon), where each actor can supply ~4 kW
NOON_SUPPLY_TIME = 43200
MIDNIGHT_SUPPLY_TIME = 0


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _noon_params():
    return AuctionParameters(
        product_type="electricity",
        gate_opening_time=38700,
        gate_closure_time=42300,
        supply_start_time=NOON_SUPPLY_TIME,
        supply_duration_s=900,
        tender_amount_kw=4.0,
        minimum_order_amount_kw=2.0,
    )


async def _controller_with_actors(*participant_ids):
    controller = Controller()
    controller.config = load_config("tests/config.json")
    controller.general_demand = create_general_demand("gd0")
    actor_ids = []
    for participant_id in participant_ids:
        actor_id, _ = await controller.register_actor(participant_id)
        actor_ids.append(actor_id)
    return controller, actor_ids


def _inject_result(controller, awarded_orders):
    controller.market.current_auction_results = [
        AuctionResult(
            auction_id="x",
            params=_noon_params(),
            clearing_price=10,
            awarded_orders=awarded_orders,
        )
    ]


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
    # GIVEN one actor without awarded orders whose battery (6 kWh available)
    # is drained by the 1 kW load over 24 midnight steps
    controller, (a,) = await _controller_with_actors("TestA")
    controller.market.current_auction_results = []
    for _ in range(24):
        controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)
    assert controller.actor_accounts[a].get_balance() == pytest.approx(0, abs=1e-6)

    # WHEN the load can no longer be covered
    controller.step_units(current_time=MIDNIGHT_SUPPLY_TIME)

    # THEN the penalty (1 kW * 250 ct) is booked on the actor's own account
    assert set(controller.actor_accounts.keys()) == {a}
    assert controller.actor_accounts[a].get_balance() == pytest.approx(-250, abs=1e-6)
    penalty_row = controller.actor_accounts[a].transactions.iloc[-1]
    assert penalty_row[AWARDED_AMOUNT] == 0
    assert penalty_row[PAYOFF] == pytest.approx(-250, abs=1e-6)
