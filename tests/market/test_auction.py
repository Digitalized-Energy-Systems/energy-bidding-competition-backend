from hackathon_backend.market.auction import *

def test_auction_clearing1():
    # Create an instance of Auction
    auction_params = AuctionParameters(
        product_type = "electricity",
        gate_opening_time = 0,
        gate_closure_time = 10,
        supply_start_time = 20,
        supply_duration_s = 10,
        tender_amount_kw = 2
    )
    auction = ElectricityAskAuction(auction_params, current_time=0)

    # Place three orders of different prices
    auction.place_order(
        amount_kw=[1],
        price_ct=10,
        agents=["a"]
    )
    auction.place_order(
        amount_kw=[2],
        price_ct=20,
        agents=["b"]
    )
    auction.place_order(
        amount_kw=[1],
        price_ct=30,
        agents=["c"]
    )

    # Clear the auction
    auction_result = auction.clear()

    # Check that the auction is cleared
    assert auction_result.clearing_price == 20
    assert len(auction_result.awarded_orders) == 2
    assert auction_result.awarded_orders[0].awarded_amount_kw == [1]
    assert auction_result.awarded_orders[1].awarded_amount_kw == [1]

def test_auction_clearing2():
    # Create an instance of Auction
    auction_params = AuctionParameters(
        product_type = "electricity",
        gate_opening_time = 0,
        gate_closure_time = 10,
        supply_start_time = 20,
        supply_duration_s = 10,
        tender_amount_kw = 2
    )
    auction = ElectricityAskAuction(auction_params, current_time=0)

    # Place three orders of different prices
    auction.place_order(
        amount_kw=[1],
        price_ct=10,
        agents=["a"]
    )
    auction.place_order(
        amount_kw=[1],
        price_ct=20,
        agents=["b"]
    )
    auction.place_order(
        amount_kw=[1],
        price_ct=30,
        agents=["c"]
    )

    # Clear the auction
    auction_result = auction.clear()

    # Check that the auction is cleared
    assert auction_result.clearing_price == 20
    assert len(auction_result.awarded_orders) == 2
    assert auction_result.awarded_orders[0].awarded_amount_kw == [1]
    assert auction_result.awarded_orders[1].awarded_amount_kw == [1]
