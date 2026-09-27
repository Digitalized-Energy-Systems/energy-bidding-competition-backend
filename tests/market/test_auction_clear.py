"""Clearing of the electricity ask auction with the list-based order API.

Covers the exact-fill case (a fully covered tender must not award a further
order 0 kW nor let it set the clearing price), the partial-fill case and the
equal share of tied prices.
"""

import itertools
import pytest
from hackathon_backend.market.auction import (
    AuctionParameters,
    ElectricityAskAuction,
    OrderError,
    initiate_electricity_ask_auction,
)


def _auction(tender_amount_kw, **params):
    return ElectricityAskAuction(
        AuctionParameters(
            product_type="electricity",
            gate_opening_time=0,
            gate_closure_time=10,
            supply_start_time=20,
            supply_duration_s=10,
            tender_amount_kw=tender_amount_kw,
            **params,
        ),
        current_time=0,
    )


def _place_three_orders(auction):
    auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[1], price_ct=20, agents=["b"])
    auction.place_order(amount_kw=[1], price_ct=30, agents=["c"])


def _awarded_by_agent(result):
    awarded = {}
    for order in result.awarded_orders:
        for agent, amount_kw in zip(order.agents, order.awarded_amount_kw):
            awarded[agent] = awarded.get(agent, 0) + amount_kw
    return awarded


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


def test_clear_partial_fill_group_order_is_shared_equally_by_its_members():
    # GIVEN a tender of 2 kW, a 1 kW order and a group order of [0.5, 1.5] kW
    auction = _auction(tender_amount_kw=2)
    auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[0.5, 1.5], price_ct=20, agents=["b", "c"])
    auction.place_order(amount_kw=[1], price_ct=30, agents=["d"])

    # WHEN
    result = auction.clear()

    # THEN the remaining 1 kW is shared equally by the members, each capped
    # at its amount
    assert len(result.awarded_orders) == 2
    assert result.clearing_price == 20
    assert result.awarded_orders[1].agents == ["b", "c"]
    assert result.awarded_orders[1].awarded_amount_kw == pytest.approx([0.5, 0.5])


def test_clear_zero_tender_awards_nothing():
    # GIVEN a tender of 0 kW, which no order fits into
    auction = _auction(tender_amount_kw=0)
    with pytest.raises(OrderError):
        auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])

    # WHEN
    result = auction.clear()

    # THEN no order receives power and there is no clearing price
    assert result.awarded_orders == []
    assert result.clearing_price is None


def test_order_above_the_tender_is_refused():
    auction = _auction(tender_amount_kw=2)
    with pytest.raises(OrderError, match="above the tender"):
        auction.place_order(amount_kw=[10], price_ct=10, agents=["a"])
    with pytest.raises(OrderError, match="above the tender"):
        auction.place_order(amount_kw=[1, 1.5], price_ct=10, agents=["a", "b"])
    assert auction.order_container.orders == []


def test_exactly_filled_tender_with_float_sums_awards_no_further_order():
    auction = _auction(0.3, minimum_order_amount_kw=0.1)
    auction.place_order(amount_kw=[0.1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[0.2], price_ct=20, agents=["b"])
    auction.place_order(amount_kw=[0.1], price_ct=30, agents=["c"])

    result = auction.clear()

    assert [order.agents for order in result.awarded_orders] == [["a"], ["b"]]
    assert result.clearing_price == 20


def test_equal_prices_share_equally_per_bidder_regardless_of_arrival_order():
    orders = [
        dict(amount_kw=[2], price_ct=10, agents=["a"]),
        dict(amount_kw=[4], price_ct=10, agents=["b"]),
        dict(amount_kw=[1], price_ct=5, agents=["c"]),
        dict(amount_kw=[1], price_ct=20, agents=["d"]),
    ]
    results = []
    for permutation in itertools.permutations(orders):
        # GIVEN a tender of 4 kW: 1 kW at 5 ct, then a level of 6 kW at 10 ct
        auction = _auction(4)
        for order in permutation:
            auction.place_order(**order)

        # WHEN
        result = auction.clear()

        # THEN the 3 kW left for the 10 ct level are shared equally
        assert _awarded_by_agent(result) == {
            "c": pytest.approx(1),
            "a": pytest.approx(1.5),
            "b": pytest.approx(1.5),
        }
        assert result.clearing_price == 10
        results.append(_awarded_by_agent(result))
    assert all(r == results[0] for r in results)


def test_equal_prices_share_equally_per_actor_also_inside_a_group_order():
    # GIVEN a level of a group order [1, 2] kW and a solo order of 3 kW
    auction = _auction(3)
    auction.place_order(amount_kw=[3], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[1, 2], price_ct=10, agents=["b", "c"])

    # WHEN
    result = auction.clear()

    # THEN the 3 kW are shared equally by the three actors
    group = next(order for order in result.awarded_orders if order.agents == ["b", "c"])
    assert group.awarded_amount_kw == pytest.approx([1.0, 1.0])
    assert _awarded_by_agent(result)["a"] == pytest.approx(1.0)


def test_clearing_price_is_that_of_the_last_level_with_an_award():
    # GIVEN three levels; the second one only fits partially
    auction = _auction(3)
    auction.place_order(amount_kw=[1], price_ct=100, agents=["a"])
    auction.place_order(amount_kw=[1], price_ct=100, agents=["b"])
    auction.place_order(amount_kw=[2], price_ct=600, agents=["c"])
    auction.place_order(amount_kw=[1], price_ct=900, agents=["d"])

    # WHEN
    result = auction.clear()

    # THEN c fills the tender partially and sets the price, d gets nothing
    assert _awarded_by_agent(result) == {
        "a": pytest.approx(1),
        "b": pytest.approx(1),
        "c": pytest.approx(1),
    }
    assert result.clearing_price == 600


def test_initiated_auction_carries_tender_and_minimum():
    auction = initiate_electricity_ask_auction(0, tender_amount=4.0, minimum_order_amount_kw=1.8)
    assert auction.params.tender_amount_kw == 4.0
    assert auction.params.minimum_order_amount_kw == 1.8
    assert "demand_amount_kw" not in auction.to_dict()["params"]
    assert "reserve_price_ct" not in auction.to_dict()["params"]


def test_flooding_a_tied_level_with_orders_gains_nothing():
    # GIVEN a tender of 3 kW and a tied level: a floods it with five orders of
    # 3 kW, b offers one order of 3 kW
    auction = _auction(3)
    for _ in range(5):
        auction.place_order(amount_kw=[3], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[3], price_ct=10, agents=["b"])

    # WHEN
    result = auction.clear()

    # THEN both get half the tender; a's half is spread over its orders
    assert _awarded_by_agent(result) == {"a": pytest.approx(1.5), "b": pytest.approx(1.5)}
    a_orders = [o for o in result.awarded_orders if o.agents == ["a"]]
    assert [o.awarded_amount_kw[0] for o in a_orders] == pytest.approx([0.3] * 5)


def test_a_small_bidder_in_a_tie_gets_its_full_amount_and_the_rest_is_shared():
    # GIVEN 5 kW for a tied level of 1, 3 and 3 kW
    auction = _auction(5)
    auction.place_order(amount_kw=[1], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[3], price_ct=10, agents=["b"])
    auction.place_order(amount_kw=[3], price_ct=10, agents=["c"])

    # WHEN / THEN a is filled, b and c share the remaining 4 kW
    assert _awarded_by_agent(auction.clear()) == {
        "a": pytest.approx(1),
        "b": pytest.approx(2),
        "c": pytest.approx(2),
    }


def test_spreading_orders_over_coalitions_gains_nothing_in_a_tie():
    # GIVEN a tender of 3 kW and a tied level: a offers alone and in two
    # group orders with different partners, b offers alone
    auction = _auction(3)
    auction.place_order(amount_kw=[2], price_ct=10, agents=["a"])
    auction.place_order(amount_kw=[1.5, 0.5], price_ct=10, agents=["a", "c"])
    auction.place_order(amount_kw=[1.5, 0.5], price_ct=10, agents=["a", "d"])
    auction.place_order(amount_kw=[2], price_ct=10, agents=["b"])

    # WHEN
    awarded = _awarded_by_agent(auction.clear())

    # THEN every actor gets the same share, capped at what it offered: c and
    # d are filled, a and b share the rest equally
    assert awarded["c"] == pytest.approx(0.5)
    assert awarded["d"] == pytest.approx(0.5)
    assert awarded["a"] == pytest.approx(1.0)
    assert awarded["b"] == pytest.approx(1.0)
