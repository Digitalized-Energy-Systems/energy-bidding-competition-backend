from typing import Any, List, Dict, Optional
import json
import os
import pandas as pd
from abc import abstractmethod, ABC
from pydantic import BaseModel
from hackathon_backend.controller import Controller
from hackathon_backend.config import Config
from hackathon_backend.market.auction import (
    Auction,
    ElectricityAskAuction,
    AuctionResult,
    AuctionParameters,
    Order,
    OrderContainer,
)
from hackathon_backend.market.cooperative import CooperativeBid, CooperativeBidPool
from hackathon_backend.accounting.account import AccountData

from hackathon_backend.units.vpp import VPP
from hackathon_backend.units.battery import MidasBatteryUnit, BatteryInformation
from hackathon_backend.units.pv import MidasPVUnit, PVInformation
from hackathon_backend.units.load import SimpleDemandUnit, DemandInformation
from hackathon_backend.units.weather import clear_sky_profile
from hackathon_backend.general_demand import create_general_demand

from hackathon_backend.accounting.account import (
    AccountData,
    to_actor_accounts,
    to_actor_account_datas,
)


class PersistenceHandler(ABC):
    @abstractmethod
    def write(self, controller: Controller):
        pass

    def load(self) -> Controller:
        pass


class AuctionData(BaseModel):
    id: str
    status: str
    params: AuctionParameters
    result: Optional[AuctionResult]
    orders: List[Order]


class MarketData(BaseModel):
    auctions: Dict[str, AuctionData]
    open_auctions: List[AuctionData]
    expired_auctions: List[AuctionData]
    current_auction_results: List[AuctionResult]


class UnitPoolData(BaseModel):
    actor_to_root_payload: Dict[str, str]


class ControllerData(BaseModel):
    registered: List[str]
    config: Config
    market: MarketData
    unit_pool: UnitPoolData
    step: int
    actor_account_data: Dict[str, AccountData]
    cooperative_bids: List[CooperativeBid] = []
    actor_to_participant: Dict[str, str] = {}
    participant_names: Dict[str, str] = {}
    pv_seed: Optional[int] = None
    general_demand_supply: Optional[str] = None
    actor_history: List[Dict[str, Any]] = []


def _to_unit(unit_information: Dict):
    if "unit_information_list" in unit_information:
        vpp = VPP()
        sub_units = [
            _to_unit(unit_dict)
            for unit_dict in unit_information["unit_information_list"]
        ]
        for unit in sub_units:
            vpp.add_unit(unit)
        return vpp
    elif "soc_percent" in unit_information:
        return MidasBatteryUnit(BatteryInformation(**unit_information))
    elif "a_m2" in unit_information:
        return MidasPVUnit(PVInformation(**unit_information))
    elif "uncertainty" in unit_information:
        return SimpleDemandUnit(DemandInformation(**unit_information))
    else:
        raise ValueError()


def _to_actor_unit_root(id_to_unit_information_root: str):
    unit_root_dict = json.loads(id_to_unit_information_root)
    return _to_unit(unit_root_dict)


def to_auction_data(auction: ElectricityAskAuction):
    return AuctionData(
        id=auction.id,
        status=auction.status,
        params=auction.params,
        result=auction.result,
        orders=auction.order_container.orders,
    )


def to_auction_data_dict(auction_data_dict: Dict[str, Auction]):
    return {id: to_auction_data(auction) for id, auction in auction_data_dict.items()}


def to_auction_data_list(auctions: List[Auction]):
    return [to_auction_data(auction) for auction in auctions]


def from_auction_data(auction_data: AuctionData):
    auction = ElectricityAskAuction(auction_data.params)
    # keep the persisted identity: the constructor draws a fresh uuid, but
    # the orders, the keys of market.auctions and the cooperative bids
    # reference the auction by its persisted id
    auction.id = auction_data.id
    container = OrderContainer()
    container.orders = auction_data.orders
    auction.order_container = container
    auction.status = auction_data.status
    auction.result = auction_data.result
    return auction


def from_auction_data_dict(auction_data_dict: Dict[str, AuctionData]):
    return {k: from_auction_data(v) for k, v in auction_data_dict.items()}


def from_auction_data_list(auction_data_list: List[AuctionData]):
    return [from_auction_data(auction_data) for auction_data in auction_data_list]


def _as_state(controller: Controller) -> ControllerData:
    return ControllerData(
        registered=sorted(controller.registered),
        config=controller.config,
        market=MarketData(
            auctions=to_auction_data_dict(controller.market.auctions),
            open_auctions=to_auction_data_list(controller.market.open_auctions),
            expired_auctions=to_auction_data_list(controller.market.expired_auctions),
            current_auction_results=controller.market.current_auction_results,
        ),
        unit_pool=UnitPoolData(
            actor_to_root_payload={
                k: v.read_full_information().model_dump_json(serialize_as_any=True)
                for k, v in controller.unit_pool.actor_to_root.items()
            }
        ),
        step=controller.step,
        actor_account_data=to_actor_account_datas(controller.actor_accounts),
        cooperative_bids=controller.cooperative_bids.all_bids(),
        actor_to_participant=controller.actor_to_participant,
        participant_names=controller.participant_names,
        pv_seed=controller.pv_seed,
        actor_history=controller.actor_history,
        general_demand_supply=(
            None
            if controller.general_demand is None
            else controller.general_demand.supply.to_json()
        ),
    )


def _load_state(controller_data: ControllerData) -> Controller:
    controller = Controller()
    controller.step = controller_data.step
    controller.actor_accounts = to_actor_accounts(controller_data.actor_account_data)
    controller.unit_pool.actor_to_root = {
        k: _to_actor_unit_root(v)
        for k, v in controller_data.unit_pool.actor_to_root_payload.items()
    }
    # the forecasts start at the last stepped step, which the unit
    # information does not carry
    last_stepped_step = max(0, controller_data.step - 1)
    for root in controller.unit_pool.actor_to_root.values():
        for unit in root.sub_units.values():
            if hasattr(unit, "time_step"):
                unit.time_step = last_stepped_step
    controller.market.auctions = from_auction_data_dict(controller_data.market.auctions)
    controller.market.open_auctions = from_auction_data_list(
        controller_data.market.open_auctions
    )
    controller.market.expired_auctions = from_auction_data_list(
        controller_data.market.expired_auctions
    )
    controller.market.current_auction_results = (
        controller_data.market.current_auction_results
    )
    controller.config = controller_data.config
    controller.registered = set(controller_data.registered)
    controller.cooperative_bids = CooperativeBidPool(controller_data.cooperative_bids)
    controller.actor_history = list(controller_data.actor_history)
    controller.actor_to_participant = dict(controller_data.actor_to_participant)
    controller.participant_names = dict(controller_data.participant_names)
    if controller_data.general_demand_supply is not None:
        controller.general_demand = create_general_demand("gd0")
        supply = pd.DataFrame.from_dict(json.loads(controller_data.general_demand_supply))
        supply.index = supply.index.astype("int64")
        controller.general_demand.supply = supply.sort_index()
    if controller_data.pv_seed is not None:
        controller.set_pv_seed(controller_data.pv_seed)
    else:
        # states from before the PV weather have clear-sky units
        controller.pv_profile = clear_sky_profile()
    return controller


class JsonPersistenceHandler:

    def __init__(self, default_fp) -> None:
        self.fp = default_fp

    def write(self, controller):
        self._write(controller, self.fp)

    def load(
        self,
    ):
        return self._load(self.fp)

    def _write(self, controller: Controller, fp):
        # atomic, so a crash while writing never leaves a truncated state
        tmp_fp = f"{fp}.tmp"
        with open(tmp_fp, "w", encoding="utf-8") as f:
            f.write(_as_state(controller).model_dump_json())
        os.replace(tmp_fp, fp)

    def _load(self, json_file) -> Controller:
        with open(json_file, encoding="utf-8") as jfp:
            json_data = jfp.read()
            return _load_state(ControllerData.model_validate_json(json_data))
