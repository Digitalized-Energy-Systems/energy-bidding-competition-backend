import uvicorn
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


def test_read_main():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=80)
