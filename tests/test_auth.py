"""Authentication with the registration key (contract section 1)."""

import uuid
from types import SimpleNamespace
import pandas as pd
import pytest
import hackathon_backend.interface as interface
from tests.helpers import COOPERATIVE_CONFIG, PLAIN_CONFIG, client

PARTICIPANTS = ["Alice42", "Bob77", {"name": "Team C", "key": "secret-c"}]
KEYS = ["Alice42", "Bob77", "secret-c"]
KEY_ERROR = "The key does not belong to the actor!"
# an auction created at 38700 s supplies at noon (43200 s)
# supplies at 19:00, the evening peak of the demand
EVENING_CREATION_TIME = 63900

ENDPOINTS = {
    "order": (
        "post",
        "/market/auction/order",
        lambda ctx: {"amount_kw": ctx.minimum, "price_ct": 10, "supply_time": ctx.supply_time},
    ),
    "propose": (
        "post",
        "/market/cooperative/propose",
        lambda ctx: {"amount_kw": 0.5, "price_ct": 10, "supply_time": ctx.supply_time},
    ),
    "join": (
        "post",
        "/market/cooperative/join",
        lambda ctx: {"cooperative_bid_id": ctx.bid_id, "amount_kw": 0.2},
    ),
    "withdraw": (
        "post",
        "/market/cooperative/withdraw",
        lambda ctx: {"cooperative_bid_id": ctx.bid_id},
    ),
    "units": ("get", "/units/information", lambda ctx: {}),
    "result": ("get", "/market/auction/result", lambda ctx: {}),
    "mine": ("get", "/market/cooperative/mine", lambda ctx: {}),
}


async def _register_all(controller):
    return [(await controller.register_actor(key))[0] for key in KEYS]


def _market_state(controller):
    orders = [
        order.model_dump()
        for auction in controller.market.auctions.values()
        for order in auction.order_container.orders
    ]
    bids = [bid.model_dump() for bid in controller.cooperative_bids.all_bids()]
    return orders, bids


@pytest.fixture
async def cooperative(api):
    """Three registered actors, an open auction and an open bid of B which A
    joined."""
    app, controller = api(COOPERATIVE_CONFIG, participants=PARTICIPANTS)
    a, b, c = await _register_all(controller)
    controller.step_market(current_time=900)
    params = controller.market.open_auctions[-1].params
    bid = await controller.propose_cooperative_bid(
        b, "Bob77", 0.5, 10, params.supply_start_time
    )
    await controller.join_cooperative_bid(a, "Alice42", bid.id, 0.2)
    ctx = SimpleNamespace(
        supply_time=params.supply_start_time,
        minimum=params.minimum_order_amount_kw,
        bid_id=bid.id,
    )
    return app, controller, (a, b, c), ctx


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_actor_endpoints_need_the_key_of_the_actor(cooperative, endpoint):
    # GIVEN
    app, controller, (a, b, c), ctx = cooperative
    method, path, endpoint_params = ENDPOINTS[endpoint]
    params = endpoint_params(ctx)
    state_before = _market_state(controller)

    async with client(app) as ac:
        send = getattr(ac, method)

        # WHEN the key of another participant, an unknown key or a display
        # name is passed
        for wrong_key in ("Bob77", "secret-c", "not-a-key", "Alice", "Team C", ""):
            response = await send(path, params={"actor_id": a, "key": wrong_key, **params})
            # THEN
            assert response.status_code == 403, (wrong_key, response.text)
            assert response.json()["detail"] == KEY_ERROR

        # WHEN the key is missing
        response = await send(path, params={"actor_id": a, **params})
        # THEN
        assert response.status_code == 422

        # WHEN the actor is unknown, even with a valid key
        response = await send(
            path, params={"actor_id": str(uuid.uuid4()), "key": "Alice42", **params}
        )
        # THEN
        assert response.status_code == 404
        assert response.json()["detail"] == "The actor id does not exist!"

        # THEN none of the refused requests changed the market
        assert _market_state(controller) == state_before

        # WHEN the right key is passed
        response = await send(path, params={"actor_id": a, "key": "Alice42", **params})
        # THEN
        assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_configured_name_and_key_participant_acts_with_its_key(cooperative):
    # GIVEN Team C, configured as {"name", "key"}
    app, controller, (a, b, c), ctx = cooperative
    assert controller.participant_display_names()[c] == "Team C"

    async with client(app) as ac:
        # THEN its name is no key
        response = await ac.post("/hackathon/register", params={"participant_id": "Team C"})
        assert response.status_code == 403
        # AND registering with the key again returns the same actor (test mode)
        response = await ac.post("/hackathon/register", params={"participant_id": "secret-c"})
        assert response.status_code == 200
        assert response.json()["actor_id"] == c

        # WHEN it acts with its key
        response = await ac.post(
            "/market/cooperative/join",
            params={"actor_id": c, "key": "secret-c", "cooperative_bid_id": ctx.bid_id, "amount_kw": 100},
        )
        # THEN
        assert response.status_code == 200, response.text
        assert response.json()["cooperative_bid"]["status"] == "closed"
        response = await ac.get("/units/information", params={"actor_id": c, "key": "secret-c"})
        assert response.status_code == 200
        response = await ac.get("/market/cooperative/mine", params={"actor_id": c, "key": "secret-c"})
        assert [bid["id"] for bid in response.json()["cooperative_bids"]] == [ctx.bid_id]


@pytest.mark.anyio
async def test_there_is_no_group_order_endpoint(api):
    # an order names one actor; power is pooled only in cooperative bids,
    # which every member joins with its own key
    app, controller = api(PLAIN_CONFIG, participants=PARTICIPANTS)
    a, b, _ = await _register_all(controller)
    controller.step_market(current_time=EVENING_CREATION_TIME)
    auction = controller.market.open_auctions[-1]
    async with client(app) as ac:
        response = await ac.post(
            "/market/auction/grouporder",
            json={"actor_ids": [a, b], "keys": ["Alice42", "Bob77"], "amount_kw": [0.5, 0.5]},
            params={"price_ct": 10, "supply_time": auction.params.supply_start_time},
        )
    assert response.status_code == 404
    assert auction.order_container.orders == []


@pytest.mark.anyio
async def test_public_endpoints_need_no_key(cooperative):
    app, controller, (a, b, c), ctx = cooperative
    async with client(app) as ac:
        for path in (
            "/market/auction/open",
            "/market/auction/price_history",
            "/account/balances",
            "/market/cooperative/open",
            "/system/demand",
            "/ui/auction/results",
            "/ui/cooperative",
            "/ui/participant_map",
        ):
            response = await ac.get(path)
            assert response.status_code == 200, path


@pytest.mark.anyio
async def test_participant_map_has_names_and_no_keys(api, workdir):
    # GIVEN a legacy participant id, a {"name", "key"} entry and an issued key
    app, controller = api(
        PLAIN_CONFIG, participants=PARTICIPANTS, issue_registration_keys=True
    )
    async with client(app) as ac:
        dave_key = (await ac.post("/hackathon/key", params={"name": "Dave"})).json()["key"]
        actor_ids = []
        for key in KEYS + [dave_key]:
            response = await ac.post("/hackathon/register", params={"participant_id": key})
            assert response.status_code == 200
            actor_ids.append(response.json()["actor_id"])

        # WHEN
        response = await ac.get("/ui/participant_map")

    # THEN every actor maps to its display name
    assert response.status_code == 200
    assert response.json() == dict(zip(actor_ids, ["Alice", "Bob", "Team C", "Dave"]))
    for key in ["Alice42", "Bob77", "secret-c", dave_key]:
        assert key not in response.text

    # AND the ranking written by the score hook shows the same names
    interface.score_handler.write(controller)
    (ranking,) = (workdir / "results").glob("agents_*.csv")
    columns = list(pd.read_csv(ranking, index_col=0).columns)
    assert columns == ["Alice", "Bob", "Team C", "Dave"]
