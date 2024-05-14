import asyncio
import random
import time
from contextlib import asynccontextmanager
import hackathon_backend.interface as interface

import logging
from pydantic import BaseModel

import uuid

from .market.market import Market, VirtualBiddingAgent, Bid

logger = logging.getLogger(__name__)

market = Market([10, 20, 30, 20, 20, 50, 20, 10], 60)

v_agent_0 = VirtualBiddingAgent()
v_agent_1 = VirtualBiddingAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # StartUp
    market.init()
    v_agent_0.init()
    v_agent_1.init()

    yield

    # ShutDown
    market.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(interface.router)
