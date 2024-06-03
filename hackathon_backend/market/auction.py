from abc import ABC, abstractmethod
from dataclasses import asdict
from pydantic import BaseModel
from typing import List
import datetime
import uuid
import logging

logger = logging.getLogger(__name__)


class AuctionParameters(BaseModel):
    product_type: str
    gate_opening_time: int
    gate_closure_time: int
    supply_start_time: int
    supply_duration_s: int
    tender_amount_kw: float = 0.0
    minimum_order_amount_kw: float = 1.0


class Order(BaseModel):
    agent: str
    amount_kw: float
    price_ct: float
    auction_id: str


class AwardedOrder(Order):
    awarded_amount_kw: float


class AuctionResult(BaseModel):
    auction_id: str
    params: AuctionParameters
    clearing_price: float
    awarded_orders: List[AwardedOrder]


class OrderContainer:
    orders: List[Order]

    def __init__(self) -> None:
        self.orders = []

    def add_order(self, order: Order):
        self.orders.append(order)


class Auction(ABC):
    status: str

    def __init__(self, params: AuctionParameters, current_time=None):
        self.id = str(uuid.uuid4())
        self.params = params
        self.result = None

    @abstractmethod
    def step(self, current_time):
        """Update auction status based on current time and perform
        time dependent actions"""

    @abstractmethod
    def place_order(self, amount_kw, price_ct, agent):
        """Place an order in the auction"""


class ElectricityAskAuction(Auction):
    """Auction at which market players can sell electricity"""

    def __init__(self, params: AuctionParameters, current_time=None):
        super().__init__(params, current_time)
        # container for all orders (only one type of order in this case)
        self.order_container = OrderContainer()

        # set auction status
        self.update_status(current_time)

    def step(self, current_time):
        # perform actions
        if self.status == "open" and current_time >= self.params.gate_closure_time:
            self.clear()

        self.update_status(current_time)

    def place_order(self, amount_kw, price_ct, agent):
        if self.status == "open" and amount_kw >= self.params.minimum_order_amount_kw:
            # create order object
            order = Order(
                auction_id=self.id, amount_kw=amount_kw, price_ct=price_ct, agent=agent
            )
            # store order
            self.order_container.add_order(order)

            logger.debug(f"Auction {self.id}: Received and stored order {order}")
        else:
            logger.debug(
                f"Auction {self.id}: Received order {order},"
                "although auction not open. Ignoring."
            )
            # TODO Raise Exception

    def update_status(self, current_time):
        if current_time is None:
            self.status = "pending"
        elif (
            current_time >= self.params.gate_opening_time
            and current_time < self.params.gate_closure_time
        ):
            self.status = "open"
        elif (
            current_time >= self.params.gate_closure_time
            and current_time
            < self.params.supply_start_time + self.params.supply_duration_s
        ):
            self.status = "closed"
        elif (
            current_time
            >= self.params.supply_start_time + self.params.supply_duration_s
        ):
            self.status = "expired"
        else:
            self.status = "pending"

    def clear(self):
        # sort orders by price
        self.order_container.orders.sort(key=lambda x: x.price_ct)
        # find awarded orders
        awarded_orders = []
        total_awarded_amount = 0
        for order in self.order_container.orders:
            if total_awarded_amount + order.amount_kw < self.params.tender_amount_kw:
                awarded_orders.append(
                    AwardedOrder(
                        auction_id=order.auction_id,
                        amount_kw=order.amount_kw,
                        price_ct=order.price_ct,
                        agent=order.agent,
                        awarded_amount_kw=order.amount_kw,
                    )
                )
                total_awarded_amount += order.amount_kw
            else:
                awarded_orders.append(
                    AwardedOrder(
                        auction_id=order.auction_id,
                        amount_kw=order.amount_kw,
                        price_ct=order.price_ct,
                        agent=order.agent,
                        awarded_amount_kw=self.params.tender_amount_kw
                        - total_awarded_amount,
                    )
                )
                break
        # find clearing price
        if len(awarded_orders) == 0:
            clearing_price = None
        else:
            clearing_price = awarded_orders[-1].price_ct

        # store result
        self.result = AuctionResult(
            auction_id=self.id,
            params=self.params,
            clearing_price=clearing_price,
            awarded_orders=awarded_orders,
        )
        return self.result

    def to_dict(self):
        return {
            "id": self.id,
            "params": self.params.model_dump(),
            "status": self.status,
        }


def initiate_electricity_ask_auction(
    current_time, tender_amount=10, minimum_order_amount_kw=1.0
):
    # Create AuctionParameters object
    auction_parameters = AuctionParameters(
        product_type="electricity",
        gate_opening_time=current_time,
        gate_closure_time=current_time + datetime.timedelta(hours=1).total_seconds(),
        supply_start_time=current_time
        + datetime.timedelta(hours=1, minutes=15).total_seconds(),
        supply_duration_s=datetime.timedelta(minutes=15).total_seconds(),
        tender_amount_kw=tender_amount,
        minimum_order_amount_kw=minimum_order_amount_kw,
    )
    # Create a new auction
    return ElectricityAskAuction(params=auction_parameters, current_time=current_time)
