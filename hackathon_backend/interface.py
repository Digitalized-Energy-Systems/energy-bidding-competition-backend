from typing import List
from fastapi import APIRouter, HTTPException
from hackathon_backend.controller import Controller, ControlException
from hackathon_backend.persistence import JsonPersistenceHandler

router = APIRouter()

controller: Controller = Controller()
persistence_handler = JsonPersistenceHandler("app_state.json")
controller.add_after_step_hook(lambda controller: persistence_handler.write(controller))


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
async def read_unit_information(actor_id: str):
    try:
        unit_information_list = await controller.read_units(actor_id)
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


@router.post("/market/auction/order")
@router.post("/market/auction/order/")
async def place_order(
    actor_id: str, amount_kw: float, price_ct: float, supply_time: int
):
    try:
        return {
            "order_ok": await controller.receive_order(
                [actor_id], [amount_kw], price_ct, supply_time
            )
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.post("/market/auction/grouporder")
@router.post("/market/auction/grouporder/")
async def place_order(
    actor_ids: List[str], amount_kws: List[float], price_ct: float, supply_time: int
):
    try:
        return {
            "order_ok": await controller.receive_order(
                actor_ids, amount_kws, price_ct, supply_time
            )
        }
    except ControlException as e:
        raise HTTPException(e.code, e.message)


@router.get("/market/auction/result")
@router.get("/market/auction/result/")
async def read_auction_result(actor_id: str):
    try:
        return await controller.return_awarded_orders(actor_id)
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
async def load_from_file():
    global controller
    controller = persistence_handler.load()
    controller.add_after_step_hook(
        lambda controller: persistence_handler.write(controller)
    )


@router.get("/ui/auction/results")
@router.get("/ui/auction/results/")
async def read_results():
    try:
        return {"results": await controller.return_auction_results()}
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
    return controller.actor_to_participant
