"""REST integration of cooperative bidding (without the stepping loop)."""

import uuid
from httpx import AsyncClient
from fastapi import FastAPI
import pytest
from hackathon_backend.main import lifespan
from hackathon_backend.config import load_config
from hackathon_backend.general_demand import DEFAULT_LOAD_PROFILE, create_general_demand
from hackathon_backend.market.cooperative import CooperativeBidPool
from hackathon_backend.market.tender import (
    supply_step_from_time,
    cooperative_minimum_order_amount_kw,
    plain_minimum_order_amount_kw,
)
from hackathon_backend.persistence import JsonPersistenceHandler
import hackathon_backend.interface as interface
from tests import helpers

COOPERATIVE_CONFIG = str(helpers.COOPERATIVE_CONFIG)
DEFAULT_CONFIG = str(helpers.PLAIN_CONFIG)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# attributes of the module-global controller which the tests here touch;
# they are restored on teardown so later tests (which share the controller)
# see it exactly as before
_SHARED_STATE = (
    "config_file",
    "config",
    "general_demand",
    "cooperative_bids",
    "step",
    "registered",
    "actor_accounts",
    "actor_to_participant",
    "participant_names",
)


def _setup(config_file):
    controller = interface.controller
    # a stepping loop left behind by an earlier test must not interfere
    controller.shutdown()
    snapshot = {name: getattr(controller, name) for name in _SHARED_STATE}
    snapshot["registered"] = set(controller.registered)
    snapshot["actor_accounts"] = dict(controller.actor_accounts)
    snapshot["actor_to_participant"] = dict(controller.actor_to_participant)
    snapshot["participant_names"] = dict(controller.participant_names)
    snapshot["actor_to_root"] = dict(controller.unit_pool.actor_to_root)

    app = FastAPI(lifespan=lifespan)
    app.include_router(interface.router)
    controller.config_file = config_file
    controller.config = load_config(config_file)
    controller.general_demand = create_general_demand("gd0")
    controller.cooperative_bids = CooperativeBidPool()
    controller.reset()
    return app, snapshot


def _teardown(snapshot):
    controller = interface.controller
    controller.reset()
    controller.cooperative_bids = CooperativeBidPool()
    controller.unit_pool.actor_to_root = snapshot.pop("actor_to_root")
    for name, value in snapshot.items():
        setattr(controller, name, value)


@pytest.fixture
async def setup_controller():
    app, snapshot = _setup(COOPERATIVE_CONFIG)
    yield app
    _teardown(snapshot)


@pytest.fixture
async def setup_controller_disabled():
    app, snapshot = _setup(DEFAULT_CONFIG)
    yield app
    _teardown(snapshot)


async def _register(participant_id):
    actor_id, _ = await interface.controller.register_actor(participant_id)
    return actor_id


FIELD = ("TestA", "TestB", "TestC", "TestD", "TestE", "TestF")


async def _fill_field():
    """Register the rest of the smallest field the design is made for (six
    actors), so the tender holds a minimum-sized order at every step."""
    registered = set(interface.controller.registered)
    for participant_id in FIELD:
        if participant_id not in registered:
            await _register(participant_id)


async def _open_auction(app):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    assert response.status_code == 200
    auctions = response.json()["auctions"]
    assert len(auctions) >= 1
    return auctions[-1]


@pytest.mark.anyio
async def test_cooperative_bidding_flow(setup_controller):
    # GIVEN six actors (the smallest field the design is made for) and one
    # open auction
    app = setup_controller
    a = await _register("TestA")
    b = await _register("TestB")
    c = await _register("TestC")
    await _fill_field()
    interface.controller.step_market(current_time=900)

    # THEN the tender of supply step 6 is the demand of the six actors and
    # the minimum order amount 1.8 kW
    auction = await _open_auction(app)
    supply_time = auction["supply_start_time"]
    assert supply_step_from_time(supply_time) == 6
    tender = round(6 * float(DEFAULT_LOAD_PROFILE[6]), 1)
    minimum = cooperative_minimum_order_amount_kw(tender)
    assert minimum == pytest.approx(1.8)
    assert auction["tender_amount_kw"] == pytest.approx(tender)
    assert auction["minimum_order_amount_kw"] == pytest.approx(minimum)

    # WHEN A proposes without target
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 1.0,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
    # THEN
    assert response.status_code == 200, response.text
    bid = response.json()["cooperative_bid"]
    assert bid["status"] == "open"
    assert bid["order_placed"] is False
    assert bid["target_amount_kw"] == pytest.approx(minimum)
    assert bid["filled_amount_kw"] == pytest.approx(1.0)
    assert bid["remaining_amount_kw"] == pytest.approx(minimum - 1.0)
    assert bid["supply_time"] == supply_time
    assert bid["price_ct"] == 10
    assert bid["members"] == [{"actor_id": a, "amount_kw": 1.0}]

    # THEN the open bids list it (and filters by supply time)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/cooperative/open")
        assert response.status_code == 200
        assert [x["id"] for x in response.json()["cooperative_bids"]] == [bid["id"]]
        response = await ac.get(
            "/market/cooperative/open/", params={"supply_time": supply_time}
        )
        assert len(response.json()["cooperative_bids"]) == 1
        response = await ac.get(
            "/market/cooperative/open", params={"supply_time": supply_time + 900}
        )
        assert response.json()["cooperative_bids"] == []

    # WHEN B joins with far more than the remaining amount
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": b,
                "key": "TestB",
                "cooperative_bid_id": bid["id"],
                "amount_kw": 100,
            },
        )
    # THEN the amount is capped, the bid closes and the group order is placed
    assert response.status_code == 200, response.text
    joined = response.json()
    assert joined["accepted_amount_kw"] == pytest.approx(minimum - 1.0)
    assert joined["cooperative_bid"]["status"] == "closed"
    assert joined["cooperative_bid"]["order_placed"] is True
    assert joined["cooperative_bid"]["remaining_amount_kw"] == pytest.approx(0.0)
    orders = interface.controller.market.auctions[bid["auction_id"]].order_container.orders
    assert len(orders) == 1
    assert orders[0].agents == [a, b]
    assert orders[0].amount_kw == pytest.approx([1.0, minimum - 1.0])
    assert orders[0].price_ct == 10

    # WHEN C joins the closed bid
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": c,
                "key": "TestC",
                "cooperative_bid_id": bid["id"],
                "amount_kw": 1,
            },
        )
    # THEN
    assert response.status_code == 409

    # WHEN A proposes with an amount that reaches the target alone
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": minimum,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
    # THEN
    assert response.status_code == 400

    # WHEN A proposes with a target above the tender
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": 10,
                "supply_time": supply_time,
                "target_amount_kw": tender + 0.5,
            },
        )
    # THEN
    assert response.status_code == 400

    # WHEN A proposes a second bid with 0.5 kW
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose/",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
    # THEN it stays open
    assert response.status_code == 200, response.text
    second_bid = response.json()["cooperative_bid"]
    assert second_bid["status"] == "open"

    # WHEN the gate of the auction closes
    interface.controller.step_market(current_time=900 + 3600)

    # THEN the unfilled bid expired and was never submitted
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get(
            "/market/cooperative/mine", params={"actor_id": a, "key": "TestA"}
        )
    assert response.status_code == 200
    mine = {x["id"]: x for x in response.json()["cooperative_bids"]}
    assert set(mine.keys()) == {bid["id"], second_bid["id"]}
    assert mine[bid["id"]]["status"] == "closed"
    assert mine[bid["id"]]["order_placed"] is True
    assert mine[second_bid["id"]]["status"] == "expired"
    assert mine[second_bid["id"]]["order_placed"] is False
    orders = interface.controller.market.auctions[bid["auction_id"]].order_container.orders
    assert len(orders) == 1

    # THEN B only sees the first bid, C none, the open list is empty
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get(
            "/market/cooperative/mine/", params={"actor_id": b, "key": "TestB"}
        )
        assert [x["id"] for x in response.json()["cooperative_bids"]] == [bid["id"]]
        response = await ac.get(
            "/market/cooperative/mine", params={"actor_id": c, "key": "TestC"}
        )
        assert response.json()["cooperative_bids"] == []
        response = await ac.get("/market/cooperative/open")
        assert response.json()["cooperative_bids"] == []

    # THEN the ui sees the toggle and both bids
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/ui/cooperative")
    assert response.status_code == 200
    ui = response.json()
    assert ui["enabled"] is True
    assert set(x["id"] for x in ui["cooperative_bids"]) == {
        bid["id"],
        second_bid["id"],
    }
    assert set(ui["cooperative_bids"][0].keys()) == {
        "id",
        "auction_id",
        "supply_time",
        "product_type",
        "price_ct",
        "target_amount_kw",
        "filled_amount_kw",
        "remaining_amount_kw",
        "status",
        "order_placed",
        "created_time",
        "members",
    }


@pytest.mark.anyio
async def test_cooperative_bidding_not_found_and_price_cap(setup_controller):
    # GIVEN
    app = setup_controller
    a = await _register("TestA")
    await _fill_field()
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    supply_time = auction["supply_start_time"]

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # unknown actor
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": str(uuid.uuid4()),
                "key": "TestA",
                "amount_kw": 1.0,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
        assert response.status_code == 404
        # no open auction for the supply time
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 1.0,
                "price_ct": 10,
                "supply_time": supply_time + 900,
            },
        )
        assert response.status_code == 404
        # amount not positive
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
        assert response.status_code == 400
        # target below the minimum
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": 10,
                "supply_time": supply_time,
                "target_amount_kw": auction["minimum_order_amount_kw"] - 0.5,
            },
        )
        assert response.status_code == 400
        # the price is capped to the maximum price of the auction
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": auction["maximum_price_ct"] + 500,
                "supply_time": supply_time,
                "target_amount_kw": auction["tender_amount_kw"],
            },
        )
        assert response.status_code == 200, response.text
        bid = response.json()["cooperative_bid"]
        assert bid["price_ct"] == auction["maximum_price_ct"]
        assert bid["target_amount_kw"] == auction["tender_amount_kw"]
        # unknown bid and unknown actor on join
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": a,
                "key": "TestA",
                "cooperative_bid_id": "nope",
                "amount_kw": 1,
            },
        )
        assert response.status_code == 404
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": str(uuid.uuid4()),
                "key": "TestA",
                "cooperative_bid_id": bid["id"],
                "amount_kw": 1,
            },
        )
        assert response.status_code == 404
        # the proposer tops up its own bid by joining again
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": a,
                "key": "TestA",
                "cooperative_bid_id": bid["id"],
                "amount_kw": 1,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["accepted_amount_kw"] == pytest.approx(1)
        assert response.json()["cooperative_bid"]["members"] == [
            {"actor_id": a, "amount_kw": 1.5}
        ]
        # unknown actor on mine
        response = await ac.get(
            "/market/cooperative/mine",
            params={"actor_id": str(uuid.uuid4()), "key": "TestA"},
        )
        assert response.status_code == 404


@pytest.mark.anyio
async def test_cooperative_bidding_disabled(setup_controller_disabled):
    # GIVEN the toggle is off
    app = setup_controller_disabled
    a = await _register("TestA")
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    # the tender follows the general demand and the minimum is the plain one:
    # 0.1 kW, the resolution of the tender
    assert auction["minimum_order_amount_kw"] == plain_minimum_order_amount_kw(
        auction["tender_amount_kw"]
    )

    # WHEN / THEN every cooperative endpoint is refused except the ui one
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 1.0,
                "price_ct": 10,
                "supply_time": auction["supply_start_time"],
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Cooperative bidding is disabled"
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": a,
                "key": "TestA",
                "cooperative_bid_id": "x",
                "amount_kw": 1.0,
            },
        )
        assert response.status_code == 403
        response = await ac.post(
            "/market/cooperative/withdraw",
            params={"actor_id": a, "key": "TestA", "cooperative_bid_id": "x"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Cooperative bidding is disabled"
        response = await ac.get("/market/cooperative/open")
        assert response.status_code == 403
        response = await ac.get(
            "/market/cooperative/mine", params={"actor_id": a, "key": "TestA"}
        )
        assert response.status_code == 403
        response = await ac.get("/ui/cooperative")
        assert response.status_code == 200
        assert response.json() == {"enabled": False, "cooperative_bids": []}
        response = await ac.get("/ui/cooperative/")
        assert response.json() == {"enabled": False, "cooperative_bids": []}


@pytest.mark.anyio
async def test_cooperative_bids_are_persisted(setup_controller, tmp_path):
    # GIVEN a proposed bid with one further member
    app = setup_controller
    a = await _register("TestA")
    b = await _register("TestB")
    c = await _register("TestC")
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    bid = await interface.controller.propose_cooperative_bid(
        a, "TestA", 0.5, 10, auction["supply_start_time"]
    )
    await interface.controller.join_cooperative_bid(b, "TestB", bid.id, 0.25)
    assert bid.status == "open"

    # WHEN the state is written and loaded again
    handler = JsonPersistenceHandler(str(tmp_path / "app_state.json"))
    handler.write(interface.controller)
    loaded = handler.load()

    # THEN the loaded controller holds the same bid
    loaded_bids = loaded.cooperative_bids.all_bids()
    assert [x.id for x in loaded_bids] == [bid.id]
    loaded_bid = loaded_bids[0]
    assert loaded_bid.status == bid.status
    assert loaded_bid.members == bid.members
    assert loaded_bid.actor_ids == [a, b]
    assert loaded_bid.auction_id == bid.auction_id
    assert loaded_bid.filled_amount_kw == pytest.approx(0.75)
    assert loaded.config.cooperative_bidding is True
    assert loaded.cooperative_bids.open_bids() == [loaded_bid]

    # THEN the restored auctions keep their persisted ids, so the bid still
    # refers to an auction the market knows
    assert set(loaded.market.auctions.keys()) == set(
        interface.controller.market.auctions.keys()
    )
    for auction_id, restored in loaded.market.auctions.items():
        assert restored.id == auction_id
    assert [x.id for x in loaded.market.open_auctions] == [
        x.id for x in interface.controller.market.open_auctions
    ]
    assert loaded_bid.auction_id in loaded.market.auctions

    # WHEN the loaded controller steps the market while the auction is open
    loaded.general_demand = create_general_demand("gd0")
    loaded.step_market(current_time=1800)

    # THEN the restored bid stays open and can still be filled and placed
    assert loaded_bid.status == "open"
    joined, accepted = await loaded.join_cooperative_bid(c, "TestC", loaded_bid.id, 100)
    assert joined is loaded_bid
    assert accepted == pytest.approx(loaded_bid.target_amount_kw - 0.75)
    assert loaded_bid.status == "closed"
    assert loaded_bid.order_placed is True
    orders = loaded.market.auctions[loaded_bid.auction_id].order_container.orders
    assert len(orders) == 1
    assert orders[0].agents == [a, b, c]
    assert orders[0].auction_id == loaded_bid.auction_id

    # AND a bid proposed after the reload can be placed as well
    auctions = await loaded.return_open_auction_params()
    new_bid = await loaded.propose_cooperative_bid(
        a, "TestA", 0.5, 10, auctions[-1]["supply_start_time"]
    )
    assert new_bid.auction_id in loaded.market.auctions
    new_bid, _ = await loaded.join_cooperative_bid(b, "TestB", new_bid.id, 100)
    assert new_bid.status == "closed"
    assert new_bid.order_placed is True


@pytest.mark.anyio
async def test_cooperative_bidding_rejects_nan(setup_controller):
    # GIVEN one open bid
    app = setup_controller
    a = await _register("TestA")
    b = await _register("TestB")
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    supply_time = auction["supply_start_time"]
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": 10,
                "supply_time": supply_time,
            },
        )
    assert response.status_code == 200, response.text
    bid = response.json()["cooperative_bid"]

    # WHEN a NaN is passed for any numeric parameter of propose
    async with AsyncClient(app=app, base_url="http://test") as ac:
        for nan_param in ("amount_kw", "price_ct", "target_amount_kw"):
            params = {
                "actor_id": a,
                "key": "TestA",
                "amount_kw": 0.5,
                "price_ct": 10,
                "supply_time": supply_time,
                nan_param: "nan",
            }
            response = await ac.post("/market/cooperative/propose", params=params)
            # THEN it is refused like the other invalid amounts
            assert response.status_code == 400, (nan_param, response.text)

        # WHEN a NaN is passed to join
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": b,
                "key": "TestB",
                "cooperative_bid_id": bid["id"],
                "amount_kw": "nan",
            },
        )
        # THEN
        assert response.status_code == 400, response.text

        # THEN the pool is unchanged, the bid is still open without B and every
        # read endpoint still serializes
        assert [x.id for x in interface.controller.cooperative_bids.all_bids()] == [
            bid["id"]
        ]
        response = await ac.get("/market/cooperative/open")
        assert response.status_code == 200
        assert response.json()["cooperative_bids"] == [bid]
        response = await ac.get(
            "/market/cooperative/mine", params={"actor_id": a, "key": "TestA"}
        )
        assert response.status_code == 200
        assert response.json()["cooperative_bids"] == [bid]
        response = await ac.get("/ui/cooperative")
        assert response.status_code == 200
        assert response.json()["cooperative_bids"] == [bid]

        # AND a finite join still fills and places the bid
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": b,
                "key": "TestB",
                "cooperative_bid_id": bid["id"],
                "amount_kw": 100,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["cooperative_bid"]["status"] == "closed"
        assert response.json()["cooperative_bid"]["order_placed"] is True


async def _withdraw(ac, actor_id, key, cooperative_bid_id):
    return await ac.post(
        "/market/cooperative/withdraw",
        params={"actor_id": actor_id, "key": key, "cooperative_bid_id": cooperative_bid_id},
    )


@pytest.mark.anyio
async def test_withdraw_from_cooperative_bid(setup_controller):
    # GIVEN a bid of A which B and C joined
    app = setup_controller
    a = await _register("TestA")
    b = await _register("TestB")
    c = await _register("TestC")
    await _fill_field()
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    bid = await interface.controller.propose_cooperative_bid(
        a, "TestA", 1.0, 10, auction["supply_start_time"]
    )
    await interface.controller.join_cooperative_bid(b, "TestB", bid.id, 0.3)
    await interface.controller.join_cooperative_bid(c, "TestC", bid.id, 0.2)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # WHEN B withdraws
        response = await _withdraw(ac, b, "TestB", bid.id)

        # THEN B's share is removed and the bid stays open
        assert response.status_code == 200, response.text
        left = response.json()["cooperative_bid"]
        assert left["status"] == "open"
        assert left["members"] == [
            {"actor_id": a, "amount_kw": 1.0},
            {"actor_id": c, "amount_kw": 0.2},
        ]
        assert left["filled_amount_kw"] == pytest.approx(1.2)
        response = await ac.get("/market/cooperative/open")
        assert [x["id"] for x in response.json()["cooperative_bids"]] == [bid.id]

        # WHEN B withdraws again, or for an unknown bid or actor
        assert (await _withdraw(ac, b, "TestB", bid.id)).status_code == 400
        assert (await _withdraw(ac, b, "TestB", "nope")).status_code == 404
        response = await _withdraw(ac, str(uuid.uuid4()), "TestB", bid.id)
        assert response.status_code == 404
        assert response.json()["detail"] == "The actor id does not exist!"
        # AND C tries it with B's key
        assert (await _withdraw(ac, c, "TestB", bid.id)).status_code == 403
        assert interface.controller.cooperative_bids.get(bid.id).actor_ids == [a, c]

        # WHEN the proposer withdraws
        response = await _withdraw(ac, a, "TestA", bid.id)

        # THEN the whole bid is cancelled
        assert response.status_code == 200, response.text
        assert response.json()["cooperative_bid"]["status"] == "withdrawn"
        response = await ac.get("/market/cooperative/open")
        assert response.json()["cooperative_bids"] == []
        response = await ac.get(
            "/market/cooperative/mine", params={"actor_id": c, "key": "TestC"}
        )
        assert [x["status"] for x in response.json()["cooperative_bids"]] == ["withdrawn"]
        # AND it can neither be joined nor left any more
        response = await ac.post(
            "/market/cooperative/join",
            params={
                "actor_id": b,
                "key": "TestB",
                "cooperative_bid_id": bid.id,
                "amount_kw": 100,
            },
        )
        assert response.status_code == 409
        assert (await _withdraw(ac, c, "TestC", bid.id)).status_code == 409

    # AND it was never placed
    auction_id = bid.auction_id
    assert interface.controller.market.auctions[auction_id].order_container.orders == []


@pytest.mark.anyio
async def test_withdraw_from_placed_bid_is_refused(setup_controller):
    # GIVEN a filled bid whose group order is placed
    app = setup_controller
    a = await _register("TestA")
    b = await _register("TestB")
    await _fill_field()
    interface.controller.step_market(current_time=900)
    auction = await _open_auction(app)
    bid = await interface.controller.propose_cooperative_bid(
        a, "TestA", 1.0, 10, auction["supply_start_time"]
    )
    await interface.controller.join_cooperative_bid(b, "TestB", bid.id, 100)
    assert bid.status == "closed" and bid.order_placed

    # WHEN a member or the proposer withdraws
    async with AsyncClient(app=app, base_url="http://test") as ac:
        responses = [
            await _withdraw(ac, b, "TestB", bid.id),
            await _withdraw(ac, a, "TestA", bid.id),
        ]

    # THEN it is refused and the order stays in the market
    assert [r.status_code for r in responses] == [409, 409]
    assert bid.status == "closed"
    orders = interface.controller.market.auctions[bid.auction_id].order_container.orders
    assert [order.agents for order in orders] == [[a, b]]
