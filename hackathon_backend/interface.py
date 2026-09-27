import time
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from hackathon_backend.controller import Controller, ControlException
from hackathon_backend.persistence import JsonPersistenceHandler
from hackathon_backend.score import CsvScoreHandler

router = APIRouter()

controller: Controller = Controller()
persistence_handler = JsonPersistenceHandler("app_state.json")
score_handler = CsvScoreHandler(time.time())


def add_after_step_hooks(controller: Controller):
    controller.add_after_step_hook(lambda controller: persistence_handler.write(controller))
    controller.add_after_step_hook(lambda controller: score_handler.write(controller))


add_after_step_hooks(controller)

logger = logging.getLogger(__name__)

@router.post("/hackathon/key")
@router.post("/hackathon/key/")
async def issue_registration_key(name: str, request: Request):
    client_ip = request.client.host if request.client else None
    try:
        return {"key": await controller.issue_registration_key(name, client_ip)}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/hackathon/register")
@router.post("/hackathon/register/")
async def register_actor(participant_id: str):
    try:
        actor_id, unit_information_list = await controller.register_actor(
            participant_id
        )
        return {
            "units": [ui.__dict__ for ui in unit_information_list],
            "actor_id": actor_id,
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/units/information")
@router.get("/units/information/")
async def read_unit_information(actor_id: str, key: str):
    try:
        unit_information_list = await controller.read_units(actor_id, key)
        return {
            "units": [ui.__dict__ for ui in unit_information_list],
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/auction/open")
@router.get("/market/auction/open/")
async def read_auctions():
    try:
        return {"auctions": await controller.return_open_auction_params()}
    except ControlException as e:
        raise HTTPException(e.code, e.message)



@router.get("/market/auction/price_history")
@router.get("/market/auction/price_history/")
async def read_market_history():
    try:
        return {"price_history": await controller.return_price_history()}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/market/auction/order")
@router.post("/market/auction/order/")
async def place_order(
    actor_id: str, key: str, amount_kw: float, price_ct: float, supply_time: int
):
    try:
        return {
            "order_ok": await controller.receive_order(
                actor_id, key, amount_kw, price_ct, supply_time
            )
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/auction/result")
@router.get("/market/auction/result/")
async def read_auction_result(actor_id: str, key: str):
    try:
        return await controller.return_awarded_orders(actor_id, key)
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/market/cooperative/propose")
@router.post("/market/cooperative/propose/")
async def propose_cooperative_bid(
    actor_id: str,
    key: str,
    amount_kw: float,
    price_ct: float,
    supply_time: int,
    target_amount_kw: Optional[float] = None,
):
    try:
        bid = await controller.propose_cooperative_bid(
            actor_id, key, amount_kw, price_ct, supply_time, target_amount_kw
        )
        return {"cooperative_bid": bid.to_dict()}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/market/cooperative/join")
@router.post("/market/cooperative/join/")
async def join_cooperative_bid(
    actor_id: str, key: str, cooperative_bid_id: str, amount_kw: float
):
    try:
        bid, accepted_amount_kw = await controller.join_cooperative_bid(
            actor_id, key, cooperative_bid_id, amount_kw
        )
        return {
            "cooperative_bid": bid.to_dict(),
            "accepted_amount_kw": accepted_amount_kw,
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/market/cooperative/withdraw")
@router.post("/market/cooperative/withdraw/")
async def withdraw_cooperative_bid(actor_id: str, key: str, cooperative_bid_id: str):
    try:
        bid = await controller.withdraw_cooperative_bid(actor_id, key, cooperative_bid_id)
        return {"cooperative_bid": bid.to_dict()}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/cooperative/open")
@router.get("/market/cooperative/open/")
async def read_open_cooperative_bids(supply_time: Optional[int] = None):
    try:
        bids = await controller.return_open_cooperative_bids(supply_time)
        return {"cooperative_bids": [bid.to_dict() for bid in bids]}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/cooperative/mine")
@router.get("/market/cooperative/mine/")
async def read_cooperative_bids_of_actor(actor_id: str, key: str):
    try:
        bids = await controller.return_cooperative_bids_of_actor(actor_id, key)
        return {"cooperative_bids": [bid.to_dict() for bid in bids]}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/account/balances")
@router.get("/account/balances/")
async def read_balances():
    try:
        return await controller.get_balance_dict()
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/system/demand")
@router.get("/system/demand/")
async def read_demand():
    try:
        return (await controller.get_gd_df()).to_json()
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/admin/load")
@router.post("/admin/load/")
async def load_from_file(admin_token: Optional[str] = None):
    global controller
    try:
        controller.check_admin_token(admin_token)
    except ControlException as e:
        raise HTTPException(e.code, e.message)
    try:
        loaded = persistence_handler.load()
    except Exception as e:
        raise HTTPException(409, f"The state could not be loaded: {e}")
    # the old loop would keep stepping a controller nobody can reach
    controller.shutdown()
    controller = loaded
    add_after_step_hooks(controller)
    controller.init()
    return {"loaded": True, "step": controller.step}


@router.get("/ui/auction/results")
@router.get("/ui/auction/results/")
async def read_results():
    try:
        return {"results": await controller.return_auction_results()}
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/ui/cooperative")
@router.get("/ui/cooperative/")
async def read_cooperative_bids_ui():
    # never raises for the toggle; the config is read at call time
    if not controller.config.cooperative_bidding:
        return {"enabled": False, "cooperative_bids": []}
    try:
        bids = await controller.return_all_cooperative_bids()
        return {
            "enabled": True,
            "cooperative_bids": [bid.to_dict() for bid in bids],
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/ui/next_step")
@router.get("/ui/next_step/")
async def seconds_until_next_step():
    return controller.remaining_sleep


@router.get("/ui/current_st")
@router.get("/ui/current_st/")
async def last_step_simulation_time():
    return controller.get_current_simulation_time_unsafe()


@router.get("/ui/participant_map")
@router.get("/ui/participant_map/")
async def participant_map():
    return controller.participant_display_names()


@router.get("/ui/status")
@router.get("/ui/status/")
async def simulation_status():
    return controller.status()


@router.get("/ui/dispatch")
@router.get("/ui/dispatch/")
async def system_dispatch():
    return await controller.system_dispatch()
