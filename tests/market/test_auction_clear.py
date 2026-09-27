"""Clearing of the electricity ask auction with the list-based order API.

Covers the exact-fill case (a fully covered tender must not award a further
order 0 kW nor let it set the clearing price) and the partial-fill case.
"""

from hackathon_backend.market.auction import (
    AuctionParameters,
    ElectricityAskAuction,
)


def _auction(tender_amount_kw):
    return ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=10,
            supply_start_time=20,
            supply_duration_s=10,
            tender_amount_kw=tender_amount_kw,
        ),
        current_time=0,
    )


def _place_three_orders(auction):
    auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[1], price_ct=20, agents=["b"])
    auction.place_order(amount_kw=[1], price_ct=30, agents=["c"])


def test_clear_exact_fill():
    # GIVEN a tender of 2 kW and three 1 kW orders
    auction = _auction(tender_amount_kw=2)
    _place_three_orders(auction)

    # WHEN
    result = auction.clear()

    # THEN the first two orders fill the tender exactly, the third one is
    # not awarded at all and does not set the clearing price
    assert len(result.awarded_orders) == 2
    assert result.clearing_price == 20
    assert result.awarded_orders[0].agents == ["a"]
    assert result.awarded_orders[0].awarded_amount_kw == [1]
    assert result.awarded_orders[1].agents == ["b"]
    assert result.awarded_orders[1].awarded_amount_kw == [1]
    assert sum(sum(o.awarded_amount_kw) for o in result.awarded_orders) == 2


def test_clear_partial_fill():
    # GIVEN a tender of 2.5 kW and three 1 kW orders
    auction = _auction(tender_amount_kw=2.5)
    _place_three_orders(auction)

    # WHEN
    result = auction.clear()

    # THEN the third order is awarded the remaining 0.5 kW and sets the price
    assert len(result.awarded_orders) == 3
    assert result.clearing_price == 30
    assert result.awarded_orders[0].awarded_amount_kw == [1]
    assert result.awarded_orders[1].awarded_amount_kw == [1]
    assert result.awarded_orders[2].agents == ["c"]
    assert result.awarded_orders[2].awarded_amount_kw == [0.5]


def test_clear_partial_fill_group_order_is_split_by_amount():
    # GIVEN a tender of 2 kW, a 1 kW order and a group order of [1, 3] kW
    auction = _auction(tender_amount_kw=2)
    auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[1, 3], price_ct=20, agents=["b", "c"])
    auction.place_order(amount_kw=[1], price_ct=30, agents=["d"])

    # WHEN
    result = auction.clear()

    # THEN the remaining 1 kW is split proportionally by amount_kw
    assert len(result.awarded_orders) == 2
    assert result.clearing_price == 20
    assert result.awarded_orders[1].agents == ["b", "c"]
    assert result.awarded_orders[1].awarded_amount_kw == [0.25, 0.75]


def test_clear_zero_tender_awards_nothing():
    # GIVEN a tender of 0 kW
    auction = _auction(tender_amount_kw=0)
    _place_three_orders(auction)

    # WHEN
    result = auction.clear()

    # THEN no order receives power and there is no clearing price
    assert result.awarded_orders == []
    assert result.clearing_price is None
