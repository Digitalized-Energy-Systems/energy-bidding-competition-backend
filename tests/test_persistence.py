"""Round trip of auctions through the persistence layer."""

from hackathon_backend.market.auction import initiate_electricity_ask_auction
from hackathon_backend.persistence import from_auction_data, to_auction_data


def test_from_auction_data_keeps_id_status_orders_and_result():
    # GIVEN a cleared auction with one group order
    auction = initiate_electricity_ask_auction(
        0, tender_amount=4.0, minimum_order_amount_kw=2.0
    )
    auction.place_order([1.0, 1.5], 10, ["A", "B"])
    auction.step(3600)
    assert auction.status == "closed"
    assert auction.result is not None

    # WHEN it is written and restored
    restored = from_auction_data(to_auction_data(auction))

    # THEN the identity the orders and cooperative bids refer to is kept
    assert restored.id == auction.id
    assert restored.status == "closed"
    assert restored.params == auction.params
    assert [o.auction_id for o in restored.order_container.orders] == [auction.id]
    assert restored.order_container.orders == auction.order_container.orders
    # AND the result is restored instead of being dropped
    assert restored.result == auction.result
    assert restored.result.awarded_orders[0].agents == ["A", "B"]


def test_from_auction_data_keeps_id_of_open_auction():
    auction = initiate_electricity_ask_auction(900, tender_amount=4.0)
    auction.step(900)
    assert auction.status == "open"
    restored = from_auction_data(to_auction_data(auction))
    assert restored.id == auction.id
    assert restored.status == "open"
    assert restored.result is None
