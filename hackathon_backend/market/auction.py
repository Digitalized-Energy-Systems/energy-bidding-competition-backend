from typing import Optional
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import List
import datetime
import math
import uuid
import logging

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE_KW = 1e-9
# an agent may take part in at most this many orders of one auction (solo
# orders and group orders count alike), which bounds the order list an
# actor can create
MAX_ORDERS_PER_AGENT = 10


class OrderError(Exception):
    """Raised by place_order for an invalid order; the message is meant for
    the client."""


class AuctionParameters(BaseModel):
    product_type: str
    gate_opening_time: int
    gate_closure_time: int
    supply_start_time: int
    supply_duration_s: int
    tender_amount_kw: float = 0.0
    minimum_order_amount_kw: float = 1.0
    maximum_price_ct: float = 1000


class Order(BaseModel):
    agents: List[str]
    amount_kw: List[float]
    price_ct: float
    auction_id: str


class AwardedOrder(Order):
    awarded_amount_kw: List[float]


class AuctionResult(BaseModel):
    auction_id: str
    params: AuctionParameters
    clearing_price: Optional[float]
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
    def place_order(self, amount_kw, price_ct, agents):
        """Validate and store an order.

        Every agent must be listed once with its own finite, positive amount;
        the total must lie between the minimum order amount and the tender;
        the price must be a finite number of at least 0 (it is capped at the
        maximum price). Non-finite values would poison the clearing (an
        infinite amount awards NaN to every order) and a negative price
        would turn a shortfall into a payout.
        """
        if self.status != "open":
            raise OrderError("Order not valid, the auction is not open!")
        agents = list(agents)
        amount_kw = list(amount_kw)
        if len(agents) == 0 or len(agents) != len(amount_kw):
            raise OrderError(
                "Order not valid, agents and amount_kw must have the same, "
                "non-zero length!"
            )
        if len(set(agents)) != len(agents):
            raise OrderError("Order not valid, an agent is listed more than once!")
        try:
            amounts_ok = all(math.isfinite(a) and a > 0 for a in amount_kw)
            price_ok = math.isfinite(price_ct) and price_ct >= 0
        except TypeError:
            raise OrderError("Order not valid, amount_kw and price_ct must be numbers!")
        if not amounts_ok:
            raise OrderError(
                "Order not valid, every amount_kw must be a finite number "
                "greater than 0!"
            )
        if not price_ok:
            raise OrderError(
                "Order not valid, the price_ct must be a finite number of at least 0!"
            )
        # limit order price
        price_ct = min(price_ct, self.params.maximum_price_ct)
        total_amount_kw = sum(amount_kw)
        # a small tolerance covers float sums of group orders
        if total_amount_kw < self.params.minimum_order_amount_kw - AMOUNT_TOLERANCE_KW:
            raise OrderError(
                "Order not valid, the amount_kw is below the minimum order amount "
                f"({self.params.minimum_order_amount_kw} kW)!"
            )
        if total_amount_kw > self.params.tender_amount_kw + AMOUNT_TOLERANCE_KW:
            raise OrderError(
                "Order not valid, the amount_kw is above the tender amount "
                f"({self.params.tender_amount_kw} kW)!"
            )
        for agent in agents:
            orders_of_agent = sum(
                1 for order in self.order_container.orders if agent in order.agents
            )
            if orders_of_agent >= MAX_ORDERS_PER_AGENT:
                raise OrderError(
                    f"Order not valid, an agent may take part in at most "
                    f"{MAX_ORDERS_PER_AGENT} orders of one auction!"
                )

        order = Order(
            auction_id=self.id,
            amount_kw=amount_kw,
            price_ct=price_ct,
            agents=agents,
        )
        self.order_container.add_order(order)
        logger.info(f"Auction {self.id}: Received and stored order {order}")

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
        # find awarded orders: an order only receives power while the tender
        # is not filled yet, so an exactly filled tender never awards a
        # further order 0 kW (which would also set the clearing price)
        epsilon = 1e-9
        awarded_orders = []
        total_awarded_amount = 0
        for order in self.order_container.orders:
            remaining = self.params.tender_amount_kw - total_awarded_amount
            if remaining <= epsilon:
                break
            order_amount_kw = sum(order.amount_kw)
            if order_amount_kw <= remaining:
                # full award
                awarded_amount_kw = list(order.amount_kw)
            else:
                # partial award, split proportionally by amount_kw
                awarded_amount_kw = [
                    amount_kw / order_amount_kw * remaining
                    for amount_kw in order.amount_kw
                ]
            awarded_orders.append(
                AwardedOrder(
                    auction_id=order.auction_id,
                    amount_kw=order.amount_kw,
                    price_ct=order.price_ct,
                    agents=order.agents,
                    awarded_amount_kw=awarded_amount_kw,
                )
            )
            total_awarded_amount += sum(awarded_amount_kw)
        # find clearing price: price of the last order that received power
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
