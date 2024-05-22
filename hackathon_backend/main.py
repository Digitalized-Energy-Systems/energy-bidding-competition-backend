import asyncio
import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
import pytest

import logging
import hackathon_backend.interface as interface

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # StartUp
    interface.controller.init()

    yield

    # ShutDown
    interface.controller.shutdown()

# FastAPI object
app = FastAPI(lifespan=lifespan)
app.include_router(interface.router)


def test_read_main():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}

@pytest.mark.anyio
async def test_read_auctions():
    # WHEN
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # response = await ac.get("/")
        # assert response.status_code == 200
        # assert response.json() == {"Hello": "World"}
        
        response = await ac.get("/market/open-auctions")
    # THEN
    assert response.status_code == 200
    assert response.json() == []

    # # GIVEN
    # interface.controller.step_market(current_time=900)
    # # WHEN
    # response = await client.get("/market/open-auctions")
    # # THEN
    # assert response.status_code == 200
    # assert len(response.json()) == 1
    
    # # GIVEN
    # interface.controller.step_market(current_time=1800)
    # # WHEN
    # response = await client.get("/market/open-auctions")
    # # THEN
    # assert response.status_code == 200
    # print(response.json())
    # assert len(response.json()) == 1