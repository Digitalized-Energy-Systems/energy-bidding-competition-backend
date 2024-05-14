import asyncio
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
import logging
from pydantic import BaseModel

import uuid

from .market import Market, VirtualBiddingAgent, Bid

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


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/market/price")
async def read_price():
    return {
        "data": [
            {"price": price, "time": i}
            for i, price in enumerate(market._clearing_prices)
        ]
    }


@app.put("/market/bid/")
async def send_bid(bid: Bid):
    market._bids.append(bid)


@app.get("/market/clearing/")
async def next_clearing():
    return market._clearing_interval - (time.time() - market._last_clearing)


@app.get("/market/bids")
async def read_bids():
    aid_to_bids = {}
    for bid in market._bids:
        if not bid.aid in aid_to_bids:
            aid_to_bids[bid.aid] = []
        aid_to_bids[bid.aid].append(bid)

    return aid_to_bids
