from typing import List, Dict, Optional
import json
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
from hackathon_backend.accounting.account import AccountData

from hackathon_backend.units.vpp import VPP
from hackathon_backend.units.battery import MidasBatteryUnit, BatteryInformation
from hackathon_backend.units.pv import MidasPVUnit, PVInformation
from hackathon_backend.units.load import SimpleDemandUnit, DemandInformation

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


def _to_unit(unit_information: Dict):
    if "unit_information_list" in unit_information:
        vpp = VPP()
        sub_units = [
            _to_unit(unit_dict)
            for unit_dict in unit_information["unit_information_list"]
        ]
        for unit in sub_units:
            vpp.add_unit(unit)
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
    container = OrderContainer()
    container.orders = auction_data.orders
    auction.order_container = container
    auction.status = auction_data.status
    auction.result = auction.result
    return auction


def from_auction_data_dict(auction_data_dict: Dict[str, AuctionData]):
    return {k: from_auction_data(v) for k, v in auction_data_dict.items()}


def from_auction_data_list(auction_data_list: List[AuctionData]):
    return [from_auction_data(auction_data) for auction_data in auction_data_list]


def _as_state(controller: Controller) -> ControllerData:
    return ControllerData(
        registered=controller.registered,
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
    )


def _load_state(controller_data: ControllerData) -> Controller:
    controller = Controller()
    controller.step = controller_data.step
    controller.actor_accounts = to_actor_accounts(controller_data.actor_account_data)
    controller.unit_pool.actor_to_root = {
        k: _to_actor_unit_root(v)
        for k, v in controller_data.unit_pool.actor_to_root_payload.items()
    }
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
    controller.registered = controller_data.registered
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
        with open(fp, "w+") as f:
            f.write(_as_state(controller).model_dump_json())

    def _load(self, json_file) -> Controller:
        with open(json_file) as jfp:
            json_data = jfp.read()
            return _load_state(ControllerData.model_validate_json(json_data))
