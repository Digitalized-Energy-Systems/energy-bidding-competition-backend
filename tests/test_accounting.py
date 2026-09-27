"""
test cases:
- test open auctions returned
- test order placed
- test auction results correctly returned
"""

import pytest
from dataclasses import dataclass
from hackathon_backend.market.market import *
from hackathon_backend.market.auction import *
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.units.weather import clear_sky_profile
from hackathon_backend.accounting.accounter import (
    PENALTY_CT_PER_KW,
    ElectricityAskAuctionAccounter as Accounter,
)
from tests.helpers import PLAIN_CONFIG


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class Order:
    agents: str
    amount_kw: float
    price_ct: float


# the market and the units are stepped directly from 15:00 on: three actors
# make the plain tender of the evening (3 * 0.4 .. 0.95 kW) big enough for a
# 1 kW order, and the clear-sky PV and the battery let the actor deliver it
START_STEP = 60


@pytest.mark.anyio
async def test_accounting():
    # GIVEN
    controller = Controller(str(PLAIN_CONFIG))
    controller.general_demand = create_general_demand("gd0")
    controller.pv_profile = clear_sky_profile()
    # set up first agent
    actor_id, units = await controller.register_actor("TestA")
    for participant_id in ("TestB", "TestC"):
        await controller.register_actor(participant_id)
    balance = None

    for i in range(20):
        current_time = (START_STEP + i) * 900
        controller.step_market(current_time=current_time)

        # WHEN
        # place order in last auction
        open_auctions = await controller.return_open_auction_params()
        placed_order = await controller.receive_order(
            actor_id,
            "TestA",
            amount_kw=1,
            price_ct=1,
            supply_time=open_auctions[-1]["supply_start_time"],
        )
        assert placed_order

        # THEN
        # check account before step, once the first order is supplied
        awarded = await controller.return_awarded_orders(actor_id, "TestA")
        if i >= 5:
            assert [o.awarded_amount_kw for o in awarded[current_time]["order"]] == [[1]]
            balance = controller.actor_accounts[actor_id].get_balance()

        controller.step_units(current_time=current_time)

        # check account after step: 1 kW delivered at 1 ct
        if balance is not None:
            assert controller.actor_accounts[actor_id].get_balance() == pytest.approx(
                balance + 1
            )

    assert controller.actor_accounts[actor_id].get_balance() == pytest.approx(15)


def test_empty_accounter():
    # GIVEN
    # WHEN
    accounter = Accounter(auction_result=None)
    # THEN
    assert accounter.return_awarded_sum("A") == 0
    assert accounter.calculate_payoff("A", 1) == 0


def test_sum_calculation():
    # GIVEN

    orders1 = [
        Order(agents=[ag], amount_kw=[1], price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [1, 2, 3])
    ]
    orders2 = [
        Order(agents=[ag], amount_kw=[1], price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [4, 5, 6])
    ]
    orders = orders1 + orders2
    auction = ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=1,
            supply_start_time=2,
            supply_duration_s=1,
            tender_amount_kw=4.5,
        ),
        current_time=0,
    )
    for order in orders:
        auction.place_order(order.amount_kw, order.price_ct, order.agents)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())

    # THEN
    assert accounter.return_awarded_sum("A") == 2
    assert accounter.return_awarded_sum("B") == 1.5
    assert accounter.return_awarded_sum("C") == 1


def test_payoff_calculation():
    # GIVEN
    orders1 = [
        Order(agents=[ag], amount_kw=[1], price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [1, 2, 3])
    ]
    orders2 = [
        Order(agents=[ag], amount_kw=[1], price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [4, 5, 6])
    ]
    orders = orders1 + orders2
    auction = ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=1,
            supply_start_time=2,
            supply_duration_s=1,
            tender_amount_kw=4.5,
        ),
        current_time=0,
    )
    for order in orders:
        auction.place_order(order.amount_kw, order.price_ct, order.agents)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())

    # THEN A is awarded 1 kW at 1 ct and 1 kW at 4 ct, B 1 kW at 2 ct and
    # 0.5 kW at 5 ct, C 1 kW at 3 ct; the provided power fills the cheapest
    # order first and every missing kW costs its price, at least the penalty
    penalty = PENALTY_CT_PER_KW
    assert penalty == 250
    assert accounter.calculate_payoff("A", 2) == 1 + 4
    assert accounter.calculate_payoff("A", 1.5) == 1 + 0.5 * 4 - 0.5 * penalty
    assert accounter.calculate_payoff("A", 1) == 1 - 1 * penalty
    assert accounter.calculate_payoff("A", 0.5) == 0.5 - 0.5 * penalty - 1 * penalty
    assert accounter.calculate_payoff("B", 2) == 2 + 0.5 * 5
    assert accounter.calculate_payoff("B", 1.5) == 2 + 0.5 * 5
    assert accounter.calculate_payoff("B", 1) == 2 - 0.5 * penalty
    assert accounter.calculate_payoff("B", 0.5) == 1 - 0.5 * penalty - 0.5 * penalty
    assert accounter.calculate_payoff("C", 2) == 3
    assert accounter.calculate_payoff("C", 1) == 3
    assert accounter.calculate_payoff("C", 0.5) == 1.5 - 0.5 * penalty


def test_shortfall_is_charged_at_the_order_price_above_the_penalty():
    auction = ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=1,
            supply_start_time=2,
            supply_duration_s=1,
            tender_amount_kw=2,
        ),
        current_time=0,
    )
    auction.place_order([2], 400, ["A"])
    accounter = Accounter(auction_result=auction.clear())

    assert accounter.calculate_payoff("A", 2) == 800
    assert accounter.calculate_payoff("A", 0.5) == 0.5 * 400 - 1.5 * 400
