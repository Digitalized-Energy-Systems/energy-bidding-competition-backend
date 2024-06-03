import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient

import logging
import hackathon_backend.interface as interface

logging.basicConfig(filename="dgh_backend.log", level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = 8000
HOST = "0.0.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # StartUp
    logger.info("Controller startup FASTAPI hook called!")
    interface.controller.init()

    yield

    # ShutDown
    logger.info("Controller shutdown FASTAPI hook called!")
    interface.controller.shutdown()


# FastAPI object
app = FastAPI(lifespan=lifespan)
app.include_router(interface.router)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
    logger.info("Started at host %s on port %s", HOST, PORT)
