# test placement of order to already closed auction
# test placement of order to non-existent auction
# test arriving message during step calculation
# test that correct orders from market result are returned to agent

import uuid
import pytest
import hackathon_backend.interface as interface
from tests.helpers import FAST_STEPPING, client, wait_until

# with four actors the plain tender (4 * 0.3 kW at night) fits a 1 kW order
PARTICIPANTS = ["TestA", "TestB", "TestC", "TestD"]


@pytest.fixture
async def setup_controller(api):
    # setup code
    app, _ = api(participants=PARTICIPANTS, **FAST_STEPPING)
    yield app  # this is where the test will start


async def _register_all(app):
    actor_ids = {}
    async with client(app) as ac:
        for participant_id in PARTICIPANTS:
            response = await ac.post(
                "/hackathon/register", params={"participant_id": participant_id}
            )
            assert response.status_code == 200, response.text
            actor_ids[participant_id] = response.json()["actor_id"]
    return actor_ids


async def _wait_for_open_auction(app):
    await wait_until(lambda: interface.controller.market.open_auctions)
    async with client(app) as ac:
        response = await ac.get("/market/auction/open")
    assert response.status_code == 200
    return response.json()["auctions"][-1]


@pytest.mark.anyio
async def test_read_auctions(setup_controller):
    # GIVEN
    # FastAPI object
    app = setup_controller
    # WHEN
    async with client(app) as ac:
        response = await ac.get("/market/auction/open")
    # THEN
    assert response.status_code == 200
    assert response.json()["auctions"] == []

    # GIVEN
    interface.controller.step_market(current_time=900)
    # WHEN
    async with client(app) as ac:
        response = await ac.get("/market/auction/open")
    # THEN
    assert response.status_code == 200
    assert len(response.json()["auctions"]) == 1

    # GIVEN
    interface.controller.step_market(current_time=1800)
    # WHEN
    async with client(app) as ac:
        response = await ac.get("/market/auction/open/")
    # THEN
    assert response.status_code == 200
    auctions = response.json()["auctions"]
    assert len(auctions) == 2


@pytest.mark.anyio
async def test_register_actor(setup_controller):
    # GIVEN
    app = setup_controller

    # WHEN
    async with client(app) as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestZ"}
        )

    # THEN
    assert response.status_code == 403

    # WHEN
    async with client(app) as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )

    # THEN
    result = response.json()
    assert response.status_code == 200
    assert len(result["units"]) == 3
    assert result["units"][0]["unit_id"] == "d0"
    assert len(result["actor_id"]) == 36

    # WHEN registering again in test mode
    async with client(app) as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )

    # THEN the same actor is returned
    assert response.status_code == 200
    assert response.json()["actor_id"] == result["actor_id"]
    assert interface.controller.registered == {"TestA"}

    # WHEN registering again outside the test mode
    interface.controller.config.test_mode = False
    async with client(app) as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )

    # THEN
    assert response.status_code == 400
    assert interface.controller.registered == {"TestA"}


@pytest.mark.anyio
async def test_read_information(setup_controller):
    # GIVEN
    app = setup_controller

    # WHEN
    async with client(app) as ac:
        response = await ac.get(
            "/units/information",
            params={"actor_id": str(uuid.uuid4()), "key": "TestA"},
        )

    # THEN
    assert response.status_code == 404

    # GIVEN
    id, _ = await interface.controller.register_actor("TestA")

    # WHEN
    async with client(app) as ac:
        response = await ac.get(
            "/units/information", params={"actor_id": str(id), "key": "TestA"}
        )

    # THEN
    result = response.json()
    assert response.status_code == 200
    assert len(result["units"]) == 3
    assert result["units"][0]["unit_id"] == "d0"


@pytest.mark.anyio
async def test_simulation_loop(setup_controller):
    # GIVEN
    app = setup_controller
    actor_ids = await _register_all(app)
    interface.controller.init()

    # WHEN
    auction = await _wait_for_open_auction(app)
    async with client(app) as ac:
        response = await ac.post(
            "/market/auction/order",
            params={
                "actor_id": actor_ids["TestA"],
                "key": "TestA",
                "amount_kw": auction["minimum_order_amount_kw"],
                "price_ct": 10,
                "supply_time": auction["supply_start_time"],
            },
        )
    order_result = response.json()

    # THEN
    assert response.status_code == 200, response.text
    assert order_result["order_ok"]


@pytest.mark.anyio
async def test_order_above_the_tender_is_refused(setup_controller):
    # GIVEN
    app = setup_controller
    actor_ids = await _register_all(app)
    interface.controller.step_market(current_time=0)
    auction = (await interface.controller.return_open_auction_params())[-1]

    # WHEN
    async with client(app) as ac:
        response = await ac.post(
            "/market/auction/order",
            params={
                "actor_id": actor_ids["TestA"],
                "key": "TestA",
                "amount_kw": 10,
                "price_ct": 10,
                "supply_time": auction["supply_start_time"],
            },
        )

    # THEN
    assert auction["tender_amount_kw"] < 10
    assert response.status_code == 400
    assert "above the tender amount" in response.json()["detail"]


@pytest.mark.anyio
async def test_full_persistence(setup_controller):
    # GIVEN
    app = setup_controller
    actor_ids = await _register_all(app)
    interface.controller.init()

    # WHEN
    auction = await _wait_for_open_auction(app)
    async with client(app) as ac:
        response = await ac.post(
            "/market/auction/order",
            params={
                "actor_id": actor_ids["TestA"],
                "key": "TestA",
                "amount_kw": auction["minimum_order_amount_kw"],
                "price_ct": 10,
                "supply_time": auction["supply_start_time"],
            },
        )
    order_result = response.json()

    # THEN
    assert response.status_code == 200, response.text
    assert order_result["order_ok"]

    # THEN PERSISTENCE: the state written after the next step holds the order
    step_of_order = interface.controller.step
    await wait_until(lambda: interface.controller.step > step_of_order)
    controller = interface.persistence_handler.load()
    assert controller is not None
    assert controller.step > step_of_order
    assert controller.registered == set(PARTICIPANTS)
    assert controller.actor_to_participant == interface.controller.actor_to_participant
    auction_id = controller.market._get_auction_id_from_supply_time_and_product_type(
        auction["supply_start_time"], "electricity"
    )
    orders = controller.market.auctions[auction_id].order_container.orders
    assert [order.agents for order in orders] == [[actor_ids["TestA"]]]
