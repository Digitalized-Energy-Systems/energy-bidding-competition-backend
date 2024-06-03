"""
test cases:
- test open auctions returned
- test order placed
- test auction results correctly returned
"""

import pytest
import asyncio, anyio
from dataclasses import dataclass
from hackathon_backend.market.market import *
from hackathon_backend.market.auction import *
from hackathon_backend.controller import Controller
from hackathon_backend.config import load_config
from hackathon_backend.accounting.accounter import (
    ElectricityAskAuctionAccounter as Accounter,
)
from fastapi import FastAPI
from hackathon_backend.main import lifespan
import hackathon_backend.interface as interface


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class Order:
    agent: str
    amount_kw: float
    price_ct: float


# @pytest.fixture
# async def setup_controller():
#     # setup code
#     app = FastAPI(lifespan=lifespan)
#     app.include_router(interface.router)
#     yield app  # this is where the test will start

#     # teardown code
#     interface.controller.reset()


@pytest.mark.anyio
async def test_accounting():  # setup_controller):
    # GIVEN
    # rng = random.Random(42)
    # set up controller
    controller = Controller()  # interface.controller
    controller.config = load_config("tests/config.json")
    controller.init()
    # set up first agent
    actor_id, units = await controller.register_actor("TestA")
    balance = None
    # wait for registration time end
    await asyncio.sleep(
        controller.config.rt_step_init_delay_s
        + controller.config.rt_step_duration_s
        + 1
    )

    for i in range(7):
        # WHEN
        # place order in last auction
        open_auctions = await controller.return_open_auction_params()
        print(open_auctions)
        placed_order = await controller.receive_order(
            actor_id,
            amount_kw=1,
            price_ct=1,
            supply_time=open_auctions[-1]["supply_start_time"],
        )
        print(f"Order placed sucessfull: {placed_order}")

        print(f"Relevant orders: \n{await controller.return_awarded_orders(actor_id)}")

        # THEN
        # check account before step
        current_time = await controller.get_current_time()
        print(f"Time: {current_time}")
        if current_time >= 5 * 900:
            balance = controller.actor_accounts[actor_id].get_balance()

        await asyncio.sleep(controller.config.rt_step_duration_s)

        # check account after step
        if balance is not None:
            assert controller.actor_accounts[actor_id].get_balance() == balance + 1

    print(f"Account transactions: {controller.actor_accounts[actor_id].transactions}")
    # assert 0 == 1


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
        Order(agent=ag, amount_kw=1, price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [1, 2, 3])
    ]
    orders2 = [
        Order(agent=ag, amount_kw=1, price_ct=pr)
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
        auction.place_order(order.amount_kw, order.price_ct, order.agent)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())

    # THEN
    assert accounter.return_awarded_sum("A") == 2
    assert accounter.return_awarded_sum("B") == 1.5
    assert accounter.return_awarded_sum("C") == 1


def test_payoff_calculation():
    # GIVEN
    orders1 = [
        Order(agent=ag, amount_kw=1, price_ct=pr)
        for ag, pr in zip(["A", "B", "C"], [1, 2, 3])
    ]
    orders2 = [
        Order(agent=ag, amount_kw=1, price_ct=pr)
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
        auction.place_order(order.amount_kw, order.price_ct, order.agent)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())

    # THEN
    assert accounter.calculate_payoff("A", 2) == 5
    assert accounter.calculate_payoff("A", 1.5) == 3
    assert accounter.calculate_payoff("A", 1) == 1
    assert accounter.calculate_payoff("A", 0.5) == 0.5
    assert accounter.calculate_payoff("B", 2) == 4.5
    assert accounter.calculate_payoff("B", 1.5) == 4.5
    assert accounter.calculate_payoff("B", 1) == 2
    assert accounter.calculate_payoff("B", 0.5) == 1
    assert accounter.calculate_payoff("C", 2) == 3
    assert accounter.calculate_payoff("C", 1) == 3
    assert accounter.calculate_payoff("C", 0.5) == 1.5
