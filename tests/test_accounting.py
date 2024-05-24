"""
test cases:
- test open auctions returned
- test order placed
- test auction results correctly returned
"""
import random
import pytest
import asyncio
from hackathon_backend.market.market import *
from hackathon_backend.market.auction import *
from hackathon_backend.controller import Controller
from hackathon_backend.accounter import (
    ElectricityAskAuctionAccounter as Accounter
)
from dataclasses import dataclass

@dataclass
class Order:
    agent: str
    amount_kw: float
    price_ct: float


def test_sum_calculation():
    # GIVEN
    
    orders1 = [Order(agent=ag, amount_kw=1, price_ct=pr) for ag, pr in zip(["A", "B", "C"], [1, 2, 3])]
    orders2 = [Order(agent=ag, amount_kw=1, price_ct=pr) for ag, pr in zip(["A", "B", "C"], [4, 5, 6])]
    orders = orders1 + orders2
    auction = ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=1,
            supply_start_time=2,
            supply_duration_s=1,
            tender_amount_kw=4.5,
        ),
        current_time=0
    )
    for order in orders:
        auction.place_order(order.amount_kw, order.price_ct, order.agent)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())
    
    # THEN
    assert accounter.return_awarded_sum("A") == 2
    assert accounter.return_awarded_sum("B") == 1.5
    assert accounter.return_awarded_sum("C") == 1
    
def test_payoff_calculation():
    # GIVEN
    orders1 = [Order(agent=ag, amount_kw=1, price_ct=pr) for ag, pr in zip(["A", "B", "C"], [1, 2, 3])]
    orders2 = [Order(agent=ag, amount_kw=1, price_ct=pr) for ag, pr in zip(["A", "B", "C"], [4, 5, 6])]
    orders = orders1 + orders2
    auction = ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=1,
            supply_start_time=2,
            supply_duration_s=1,
            tender_amount_kw=4.5,
        ),
        current_time=0
    )
    for order in orders:
        auction.place_order(order.amount_kw, order.price_ct, order.agent)
    # WHEN
    accounter = Accounter(auction_result=auction.clear())
    
    # THEN
    assert accounter.calculate_payoff("A", 2) == 5
    assert accounter.calculate_payoff("A", 1.5) == 3
    assert accounter.calculate_payoff("A", 1) == 1
    assert accounter.calculate_payoff("A", 0.5) == 0.5
    assert accounter.calculate_payoff("B", 2) == 4.5
    assert accounter.calculate_payoff("B", 1.5) == 4.5
    assert accounter.calculate_payoff("B", 1) == 2
    assert accounter.calculate_payoff("B", 0.5) == 1
    assert accounter.calculate_payoff("C", 2) == 3
    assert accounter.calculate_payoff("C", 1) == 3
    assert accounter.calculate_payoff("C", 0.5) == 1.5