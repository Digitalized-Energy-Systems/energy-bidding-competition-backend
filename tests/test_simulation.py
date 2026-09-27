import pytest
import hackathon_backend.interface as interface
from tests.helpers import COOPERATIVE_CONFIG, FAST_STEPPING, client, wait_until

PARTICIPANTS = ["TestA", "TestB", "TestC", "TestD"]


@pytest.fixture
async def setup_controller(api):
    # setup code
    app, _ = api(participants=PARTICIPANTS, **FAST_STEPPING)
    yield app  # this is where the test will start


async def _register(app, participant_id):
    async with client(app) as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": participant_id}
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _open_auctions(app):
    await wait_until(lambda: interface.controller.market.open_auctions)
    async with client(app) as ac:
        response = await ac.get("/market/auction/open")
    return response.json()


@pytest.mark.anyio
async def test_simulation_loop(setup_controller):
    # GIVEN
    app = setup_controller
    register_result = await _register(app, "TestA")
    for participant_id in PARTICIPANTS[1:]:
        await _register(app, participant_id)
    interface.controller.init()

    # WHEN
    auction_result = await _open_auctions(app)
    async with client(app) as ac:
        response = await ac.post(
            "/market/auction/order",
            params={
                "actor_id": register_result["actor_id"],
                "key": "TestA",
                "amount_kw": 1,
                "price_ct": 10,
                "supply_time": auction_result["auctions"][-1]["supply_start_time"],
            },
        )
    order_result = response.json()

    # THEN
    assert response.status_code == 200, response.text
    assert order_result["order_ok"]


@pytest.mark.anyio
async def test_simulation_loop_cooperative_bid_phase_2(api):
    # GIVEN the cooperative mode
    app, _ = api(COOPERATIVE_CONFIG, participants=PARTICIPANTS, **FAST_STEPPING)
    register_resultA = await _register(app, "TestA")
    register_resultB = await _register(app, "TestB")
    for participant_id in PARTICIPANTS[2:]:
        await _register(app, participant_id)
    a, b = register_resultA["actor_id"], register_resultB["actor_id"]
    interface.controller.init()

    # WHEN A proposes 0.5 kW at 10 ct and B fills the bid
    auction = (await _open_auctions(app))["auctions"][-1]
    supply_time = auction["supply_start_time"]
    target = auction["minimum_order_amount_kw"]
    async with client(app) as ac:
        response = await ac.post(
            "/market/cooperative/propose",
            params={"actor_id": a, "key": "TestA", "amount_kw": 0.5, "price_ct": 10, "supply_time": supply_time},
        )
        assert response.status_code == 200, response.text
        bid_id = response.json()["cooperative_bid"]["id"]
        response = await ac.post(
            "/market/cooperative/join",
            params={"actor_id": b, "key": "TestB", "cooperative_bid_id": bid_id, "amount_kw": 100},
        )

    # THEN the group order is placed
    assert response.status_code == 200, response.text
    assert response.json()["cooperative_bid"]["order_placed"] is True

    # WHEN the supply interval of the auction is settled
    await wait_until(lambda: interface.controller.step > supply_time // 900)
    async with client(app) as ac:
        response = await ac.get("/account/balances")

    # THEN the awarded group order is credited to the members' own accounts:
    # the balances dict holds exactly the registered actors, no joint key,
    # and each member earned its amount at 10 ct
    balances = response.json()
    assert response.status_code == 200
    assert set(balances.keys()) == set(interface.controller.actor_to_participant)
    assert balances[a] == pytest.approx(10 * 0.5)
    assert balances[b] == pytest.approx(10 * (target - 0.5))
