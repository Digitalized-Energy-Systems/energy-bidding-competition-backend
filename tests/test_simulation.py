import json, uuid, asyncio
from httpx import AsyncClient
from fastapi import FastAPI
import pytest
from hackathon_backend.main import lifespan
from hackathon_backend.config import load_config
import hackathon_backend.interface as interface


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def setup_controller():
    # setup code
    app = FastAPI(lifespan=lifespan)
    app.include_router(interface.router)
    interface.controller.config_file = "tests/config.json"
    interface.controller.config = load_config("tests/config.json")
    yield app  # this is where the test will start

    # teardown code
    interface.controller.reset()


@pytest.mark.anyio
async def test_simulation_loop(setup_controller):
    # GIVEN
    app = setup_controller
    app.include_router(interface.router)
    interface.controller.init()
    config = interface.controller.config

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )
    register_result = response.json()
    print(register_result)
    await asyncio.sleep(config.rt_step_duration_s + config.rt_step_init_delay_s + 1)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    auction_result = response.json()
    print(auction_result)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/auction/order",
            params={
                "actor_id": register_result["actor_id"],
                "amount_kw": 10,
                "price_ct": 10,
                "supply_time": auction_result["auctions"][0]["supply_start_time"],
            },
        )
    order_result = response.json()

    # THEN
    assert response.status_code == 200
    assert order_result["order_ok"]


@pytest.mark.anyio
async def test_simulation_loop_group_order_phase_2(setup_controller):
    # GIVEN
    app = setup_controller
    app.include_router(interface.router)
    interface.controller.init()
    config = interface.controller.config

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )
    register_resultA = response.json()
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestB"}
        )
    register_resultB = response.json()
    print(register_resultA)
    print(register_resultB)

    await asyncio.sleep(config.rt_step_duration_s + config.rt_step_init_delay_s + 1)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    auction_result = response.json()
    print(auction_result)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/market/auction/grouporder",
            json={
                "actor_ids": [
                    register_resultA["actor_id"],
                    register_resultB["actor_id"],
                ],
                "amount_kw": [10, 10],
            },
            params={
                "price_ct": 10,
                "supply_time": auction_result["auctions"][0]["supply_start_time"],
            },
        )
    order_result = response.json()

    # THEN
    assert response.status_code == 200
    assert order_result["order_ok"]

    # WHEN
    await asyncio.sleep(config.rt_step_duration_s)
    await asyncio.sleep(config.rt_step_duration_s)
    await asyncio.sleep(config.rt_step_duration_s)
    await asyncio.sleep(config.rt_step_duration_s + 1)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/account/balances")

    # THEN
    assert round(list(response.json().items())[2][1]) == 3
