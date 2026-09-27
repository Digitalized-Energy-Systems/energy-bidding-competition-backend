"""Order slots per actor and auction (contract section 4): at most 10
orders, and every open cooperative bid an actor is a member of reserves one
of them, so a filled bid can always be placed."""

import pytest
from hackathon_backend.controller import ControlException, Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.market.auction import MAX_ORDERS_PER_AGENT
from tests.helpers import COOPERATIVE_CONFIG, client

# the auction created at 38700 s supplies at noon; with six actors the tender
# is 2.1 kW and the minimum 1.8 kW
NOON_SUPPLY_TIME = 43200
CREATION_TIME = NOON_SUPPLY_TIME - 4500
SLOT_ERROR = "orders of one auction, open cooperative bids included"
FIELD = ("TestA", "TestB", "TestC", "TestD", "TestE", "TestF")


class Market:
    def __init__(self, controller, actors, auction):
        self.controller = controller
        self.actors = actors
        self.auction = auction

    async def order(self, key, amount_kw=1.8):
        return await self.controller.receive_order(self.actors[key], key, amount_kw, 10, NOON_SUPPLY_TIME)

    async def propose(self, key, amount_kw=1.0):
        return await self.controller.propose_cooperative_bid(
            self.actors[key], key, amount_kw, 10, NOON_SUPPLY_TIME
        )

    async def join(self, key, bid, amount_kw):
        return await self.controller.join_cooperative_bid(
            self.actors[key], key, bid.id, amount_kw
        )

    async def withdraw(self, key, bid):
        return await self.controller.withdraw_cooperative_bid(self.actors[key], key, bid.id)

    def orders_of(self, key):
        return sum(
            1 for order in self.auction.order_container.orders if self.actors[key] in order.agents
        )


@pytest.fixture
async def market():
    controller = Controller(str(COOPERATIVE_CONFIG))
    controller.general_demand = create_general_demand("gd0")
    actors = {}
    for key in FIELD:
        actors[key], _ = await controller.register_actor(key)
    controller.step_market(current_time=CREATION_TIME)
    auction = controller.market.open_auctions[-1]
    assert auction.params.minimum_order_amount_kw == 1.8
    assert auction.params.tender_amount_kw == 2.1
    return Market(controller, actors, auction)


async def _refused(request):
    with pytest.raises(ControlException) as e:
        await request
    assert e.value.code == 400
    assert SLOT_ERROR in e.value.message


@pytest.mark.anyio
async def test_open_membership_reserves_a_slot_so_the_filled_bid_is_placed(market):
    # GIVEN A proposed a bid and filled the remaining 9 slots with orders
    bid = await market.propose("TestA")
    for _ in range(MAX_ORDERS_PER_AGENT - 1):
        await market.order("TestA")
    other = await market.propose("TestB")

    # THEN the 11th slot is refused for an order, a proposal and joining a
    # further bid
    await _refused(market.order("TestA"))
    await _refused(market.propose("TestA"))
    await _refused(market.join("TestA", other, 0.5))
    assert market.orders_of("TestA") == MAX_ORDERS_PER_AGENT - 1
    assert not other.has_member(market.actors["TestA"])

    # AND a top-up of the own bid needs no new slot
    _, accepted = await market.join("TestA", bid, 0.5)
    assert accepted == pytest.approx(0.5)

    # WHEN B fills the bid
    bid, _ = await market.join("TestB", bid, 100)

    # THEN its group order is placed as A's 10th order
    assert bid.status == "closed"
    assert bid.order_placed is True
    assert market.orders_of("TestA") == MAX_ORDERS_PER_AGENT


@pytest.mark.anyio
async def test_member_tops_up_but_cannot_join_a_new_bid_without_a_slot(market):
    # GIVEN B is a member of A's bid and took 9 further slots
    bid = await market.propose("TestA")
    await market.join("TestB", bid, 0.3)
    for _ in range(MAX_ORDERS_PER_AGENT - 1):
        await market.order("TestB")
    other = await market.propose("TestC")

    # THEN B can top up the bid it is a member of, but joins no other bid
    _, accepted = await market.join("TestB", bid, 0.2)
    assert accepted == pytest.approx(0.2)
    assert bid.member(market.actors["TestB"]).amount_kw == pytest.approx(0.5)
    await _refused(market.join("TestB", other, 0.5))
    await _refused(market.propose("TestB"))


@pytest.mark.anyio
async def test_withdrawing_frees_the_reserved_slot(market):
    # GIVEN A's own bid, a membership in B's bid and 8 orders
    own = await market.propose("TestA")
    joined = await market.propose("TestB")
    await market.join("TestA", joined, 0.5)
    for _ in range(MAX_ORDERS_PER_AGENT - 2):
        await market.order("TestA")
    await _refused(market.order("TestA"))

    # WHEN A leaves B's bid, one order fits again
    await market.withdraw("TestA", joined)
    assert await market.order("TestA")
    await _refused(market.order("TestA"))

    # WHEN A cancels its own bid, the last slot is free
    await market.withdraw("TestA", own)
    assert own.status == "withdrawn"
    assert await market.order("TestA")
    assert market.orders_of("TestA") == MAX_ORDERS_PER_AGENT


@pytest.mark.anyio
async def test_slot_limit_is_a_bad_request_at_the_api(api):
    # GIVEN A holds 10 slots
    app, controller = api(COOPERATIVE_CONFIG)
    actors = {}
    for key in FIELD:
        actors[key], _ = await controller.register_actor(key)
    controller.step_market(current_time=CREATION_TIME)
    await controller.propose_cooperative_bid(actors["TestA"], "TestA", 1.0, 10, NOON_SUPPLY_TIME)
    for _ in range(MAX_ORDERS_PER_AGENT - 1):
        await controller.receive_order(actors["TestA"], "TestA", 1.8, 10, NOON_SUPPLY_TIME)
    other = await controller.propose_cooperative_bid(
        actors["TestB"], "TestB", 1.0, 10, NOON_SUPPLY_TIME
    )
    a = {"actor_id": actors["TestA"], "key": "TestA"}

    # WHEN / THEN
    async with client(app) as ac:
        responses = [
            await ac.post(
                "/market/auction/order",
                params={**a, "amount_kw": 1.8, "price_ct": 10, "supply_time": NOON_SUPPLY_TIME},
            ),
            await ac.post(
                "/market/cooperative/propose",
                params={**a, "amount_kw": 1.0, "price_ct": 10, "supply_time": NOON_SUPPLY_TIME},
            ),
            await ac.post(
                "/market/cooperative/join",
                params={**a, "cooperative_bid_id": other.id, "amount_kw": 0.5},
            ),
        ]
    for response in responses:
        assert response.status_code == 400, response.text
        assert SLOT_ERROR in response.json()["detail"]
