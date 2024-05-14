import asyncio
import random
import time

import logging
from pydantic import BaseModel

import uuid

class Bid(BaseModel):
    aid: str
    interval: int
    price: float
    amount: float


class PublicAuction(BaseModel):
    # Auction for Participants
    pass

class FullAuction(PublicAuction):
    # Full Auction Information
    pass

class AuctionBoard(BaseModel):
    # List of auctions
    pass

class Auction:
    # Internal Auction
    pass

class Market:
    def __init__(self, demands, clearing_interval) -> None:
        self._clearing_prices = []
        self._demands = demands
        self._bids = []
        self._main_loop = None
        self._last_clearing = 0
        self._clearing_interval = clearing_interval
        self._current_interval = 0

    def clear_market(self):
        if self._current_interval > len(self._demands):
            self._last_clearing = 0
            return
        current_interval_bids = [
            bid for bid in self._bids if bid.interval == self._current_interval
        ]
        current_interval_bids.sort(key=lambda v: v.price)
        amount = 0
        for bid in current_interval_bids:
            if amount >= self._demands[self._current_interval]:
                self._clearing_prices.append(bid.price)
                break
            amount += bid.amount
        self._current_interval += 1
        self._last_clearing = time.time()

    async def update_market(self):
        await asyncio.sleep(10)
        while True:
            await asyncio.sleep(self._clearing_interval)
            self.clear_market()

    def init(self):
        self._main_loop = asyncio.create_task(self.update_market())

    def shutdown(self):
        try:
            self._main_loop.cancel()
        except:
            pass


class VirtualBiddingAgent:
    def __init__(self) -> None:
        self._aid = str(uuid.uuid1())
        self._bid_counter = 0

    async def bid(self, sleep_time_in_s):
        while True:
            await asyncio.sleep(sleep_time_in_s)
            bid = Bid(
                aid=self._aid,
                interval=self._bid_counter,
                price=random.random() * 10,
                amount=15 + random.random() * 10,
            )
            self._bid_counter += 1
            await send_bid(bid)

    def init(self):
        self._main_loop = asyncio.create_task(self.bid(60))