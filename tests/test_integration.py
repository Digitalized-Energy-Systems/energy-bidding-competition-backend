# test placement of order to already closed auction
# test placement of order to non-existent auction
# test arriving message during step calculation
# test that correct orders from market result are returned to agent

import uuid
from httpx import AsyncClient
from fastapi import FastAPI
import pytest
from hackathon_backend.main import lifespan
from hackathon_backend.controller import load_config
import hackathon_backend.interface as interface


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def setup_controller():
    # setup code
    app = FastAPI(lifespan=lifespan)
    app.include_router(interface.router)
    interface.controller.config = load_config("tests/config.json")
    yield app  # this is where the test will start

    # teardown code
    interface.controller.reset()


@pytest.mark.anyio
async def test_read_auctions(setup_controller):
    # GIVEN
    # FastAPI object
    app = setup_controller
    app.include_router(interface.router)
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    # THEN
    assert response.status_code == 200
    assert response.json()['auctions'] == []

    # GIVEN
    interface.controller.step_market(current_time=900)
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    # THEN
    assert response.status_code == 200
    print(response.json())
    assert len(response.json()['auctions']) == 1

    # GIVEN
    interface.controller.step_market(current_time=1800)
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open/")
    # THEN
    assert response.status_code == 200
    print(response.json())
    assert len(response.json()['auctions']) == 2


@pytest.mark.anyio
async def test_register_actor(setup_controller):
    # GIVEN
    app = setup_controller
    app.include_router(interface.router)

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestZ"}
        )

    # THEN
    assert response.status_code == 403

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )

    # THEN
    result = response.json()
    assert response.status_code == 200
    assert len(result["units"]) == 3
    assert result["units"][0]["unit_id"] == "d0"
    assert len(result["actor_id"]) == 36

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post(
            "/hackathon/register", params={"participant_id": "TestA"}
        )

    # THEN
    assert response.status_code == 400
    assert len(interface.controller.registered) == 1
    assert interface.controller.registered.pop() == "TestA"


@pytest.mark.anyio
async def test_read_information(setup_controller):
    # GIVEN
    app = setup_controller
    app.include_router(interface.router)

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/units/information", params={"actor_id": uuid.uuid4()})

    # THEN
    assert response.status_code == 404

    # GIVEN
    id, _ = await interface.controller.register_actor("TestA")

    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/units/information", params={"actor_id": str(id)})

    # THEN
    result = response.json()
    assert response.status_code == 200
    assert len(result["units"]) == 3
    assert result["units"][0]["unit_id"] == "d0"
