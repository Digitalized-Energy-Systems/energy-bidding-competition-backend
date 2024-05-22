from typing import List
from fastapi import APIRouter, HTTPException
from hackathon_backend.controller import Controller, ControlException
from hackathon_backend.market.auction import Order, AwardedOrder
from hackathon_backend.units.pool import UnitInformation


router = APIRouter()

controller = Controller()


@router.get("/")
async def read_root():
    return {"Hello": "World"}


@router.post("/hackathon/register")
async def register_actor(participant_id: str) -> List[UnitInformation]:
    try:
        return await controller.register_agent(participant_id)
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/units/information")
async def read_unit_information(actor_id: str) -> UnitInformation:
    try:
        return await controller.read_units(actor_id)
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/auction/open")
async def read_auctions():
    try:
        return await controller.return_open_auction_params()
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.put("/market/order")
async def place_order(actor_id: str, order: Order, supply_time=None):
    try:
        return await controller.receive_order(actor_id, order, supply_time=supply_time)
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/auction/result")
async def read_auction_result(actor_id: str):
    try:
        return await controller.return_awarded_orders(actor_id)
    except ControlException as e:
        raise HTTPException(e.code, e.message)


# @router.get("/market/price")
# async def read_price():
#     return {
#         "data": [
#             {"price": price, "time": i}
#             for i, price in enumerate(market._clearing_prices)
#         ]
#     }


# @router.put("/market/bid/")
# async def send_bid(bid: Bid):
#     market._bids.append(bid)


# @router.get("/market/clearing/")
# async def next_clearing():
#     return market._clearing_interval - (time.time() - market._last_clearing)


# @router.get("/market/bids")
# async def read_bids():
#     aid_to_bids = {}
#     for bid in market._bids:
#         if not bid.aid in aid_to_bids:
#             aid_to_bids[bid.aid] = []
#         aid_to_bids[bid.aid].append(bid)

#     return aid_to_bids
