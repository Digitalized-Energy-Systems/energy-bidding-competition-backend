import asyncio
import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


client = TestClient(app)

def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}

def test_read_auctions():
    response = client.get("/market/open-auctions")
    assert response.status_code == 200
    assert response.json() == []

    interface.controller.step_market(current_time=900)
    
    response = client.get("/market/open-auctions")
    assert response.status_code == 200
    assert response.json() == []