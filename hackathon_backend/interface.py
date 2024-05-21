from fastapi import APIRouter
from hackathon_backend.controller import Controller

router = APIRouter()

controller = Controller()

@router.get("/")
async def read_root():
    return {"Hello": "World"}

@router.get("/market/open-auctions")
async def read_auctions():
    # TODO correct way to use controller
    return controller.return_open_auction_params()


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
