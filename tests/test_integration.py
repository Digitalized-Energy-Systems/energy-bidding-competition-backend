# test placement of order to already closed auction
# test placement of order to non-existent auction
# test arriving message during step calculation
# test that correct orders from market result are returned to agent

from httpx import AsyncClient
from fastapi import FastAPI
import pytest
from hackathon_backend.main import lifespan
import hackathon_backend.interface as interface

@pytest.fixture
async def setup_controller():
    # setup code
    app = FastAPI(lifespan=lifespan)
    app.include_router(interface.router)
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
    assert response.json() == []

    # GIVEN
    interface.controller.step_market(current_time=900)
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open")
    # THEN
    assert response.status_code == 200
    print(response.json())
    assert len(response.json()) == 1
    
    # GIVEN
    interface.controller.step_market(current_time=1800)
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/market/auction/open/")
    # THEN
    assert response.status_code == 200
    print(response.json())
    assert len(response.json()) == 2