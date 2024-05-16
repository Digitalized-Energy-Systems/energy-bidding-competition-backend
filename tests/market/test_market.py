"""
test cases:
- test open auctions returned
- test order placed
- test auction results correctly returned
"""
import datetime 
import random
from hackathon_backend.market.market import *
from hackathon_backend.market.auction import *

def test_open_auctions_returned():
    market = Market()
    
    # loop over time steps
    for time_index in range(0, 20):
        
        # setup time and inputs
        current_time = time_index * 900
        market_inputs = MarketInputs()
        market_inputs._now_dt=datetime.datetime.fromtimestamp(current_time) # TODO insert correct time
        market_inputs.step_size=900
        market.inputs = market_inputs
        
        # Create AuctionParameters object
        auction_time = current_time # - 1800
        auction_parameters = AuctionParameters(
            product_type="electricity",
            gate_opening_time=auction_time,
            gate_closure_time=auction_time + datetime.timedelta(hours=1).total_seconds(),
            supply_start_time=auction_time + datetime.timedelta(hours=1, minutes=15).total_seconds(),
            supply_duration_s=datetime.timedelta(minutes=15).total_seconds(),
            tender_amount_kw=2
        )
        # Create a new auction
        new_auction = ElectricityAskAuction(
            params=auction_parameters,
            current_time=current_time
        )
        market.receive_auction(new_auction)
        
        market.step()

        # check that auctions were opened (and closed)
        open_auctions = market.get_open_auctions()
        print(open_auctions)
        # at most four auctions are opened, because they are 
        # closed after 60 minutes
        assert len(open_auctions) == min(time_index + 1, 4)
        for auction in open_auctions:
            assert len(market.auctions[auction["id"]].order_container.orders) == 0
        
        
def test_order_placed():
    rng = random.Random(42)
    market = Market()
    
    # loop over time steps
    for time_index in range(0, 20):
        ## Market
        # setup time and inputs
        current_time = time_index * 900
        market_inputs = MarketInputs()
        market_inputs._now_dt=datetime.datetime.fromtimestamp(current_time) # TODO insert correct time
        market_inputs.step_size=900
        market.inputs = market_inputs
        
        # Create AuctionParameters object
        auction_time = current_time # - 1800
        auction_parameters = AuctionParameters(
            product_type="electricity",
            gate_opening_time=auction_time,
            gate_closure_time=auction_time + datetime.timedelta(hours=1).total_seconds(),
            supply_start_time=auction_time + datetime.timedelta(hours=1, minutes=15).total_seconds(),
            supply_duration_s=datetime.timedelta(minutes=15).total_seconds(),
            tender_amount_kw=2
        )
        # Create a new auction
        new_auction = ElectricityAskAuction(
            params=auction_parameters,
            current_time=current_time
        )
        market.receive_auction(new_auction)
        
        market.step()

        ## Agent
        # check that auctions were opened (and closed)
        open_auctions = market.get_open_auctions()
        
        market.receive_order(
            amount_kw=1,
            price_ct=rng.random(),
            agent="agent1",
            supply_time=current_time + 5*900,
            product_type="electricity"
        )
        market.receive_order(
            amount_kw=1.5,
            price_ct=rng.random(),
            agent="agent2",
            supply_time=current_time + 5*900,
            product_type="electricity"
        )
        market.receive_order(
            amount_kw=2,
            price_ct=rng.random(),
            agent="agent3",
            supply_time=current_time + 5*900,
            product_type="electricity"
        )
        
        ## Checks
        # number of orders
        for auction in open_auctions:
            print(market.auctions[auction["id"]].order_container.orders)
            assert len(market.auctions[auction["id"]].order_container.orders) == 3
        
        # auction results sum of awarded amounts
        auction_results = market.get_current_auction_results()
        print(auction_results)
        if  len(auction_results) == 1:
            2 == [sum([order.awarded_amount_kw for order in result.awarded_orders]) for result in auction_results]
        elif len(auction_results) > 1:
            assert all(2 == sum([order.awarded_amount_kw for order in result.awarded_orders]) for result in auction_results)
        
        # number of awarded orders
        # check if smaller than or equal to two
        number_awarded_orders = [len(result.awarded_orders) for result in auction_results]
        if number_awarded_orders:
            assert 2 >= min(number_awarded_orders)
    # assert 1 == 0