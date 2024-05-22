import asyncio
import random
import time, datetime
from pydantic import BaseModel
from .market.market import Market, MarketInputs
from .market.auction import AuctionParameters, ElectricityAskAuction

class Controller:
    """
    Needed functionality:
    - agent management
      - accept agent registration
      - store agent identifiers
    - unit management
      - [...]
    - loop over time
    - lock when new time interval is reached
    - return full auction results for gui
    First version done:
    - create auctions and pass them to market
    - order handling
      - pass orders to market
      - return error in case of invalid order
    - return open auctions from market
    - return auction results from market
      - filter according to asking agent
      - return full results for gui
    """
    def __init__(self):
        self.market = Market()
        self.current_task = asyncio.Future()
        self.current_task.set_result(None)

    def init(self):
        self._main_loop = asyncio.create_task(self.update_market())
        
    async def update_market(self):
        # TODO Time for agents to register
        await asyncio.sleep(20)
        
        while True:
            self.current_task = asyncio.create_task(self.loop())
            await asyncio.sleep(300)
        
    async def loop(self):
        # TODO step 15 minutes ahead
        current_time=time.time()
        self.step_market(current_time=current_time)
    
    async def check_step_done(self):
        if not self.current_task.done():
            await self.current_task
    
    def step_market(self, current_time):
        market_inputs = MarketInputs()
        market_inputs._now_dt=datetime.datetime.fromtimestamp(current_time) # TODO insert correct time
        market_inputs.step_size=900
        self.market.inputs = market_inputs

        self.initiate_electricity_ask_auction(current_time)        
        
        self.market.step()
    
    def initiate_electricity_ask_auction(self, current_time):
        # Create AuctionParameters object
        auction_parameters = AuctionParameters(
            product_type="electricity",
            gate_opening_time=current_time,
            gate_closure_time=current_time + datetime.timedelta(hours=1).total_seconds(),
            supply_start_time=current_time + datetime.timedelta(hours=1, minutes=15).total_seconds(),
            supply_duration_s=datetime.timedelta(minutes=15).total_seconds(),
            tender_amount_kw=10, # TODO adjust tender amount
        )
        # Create a new auction
        new_auction = ElectricityAskAuction(
            params=auction_parameters,
            current_time=current_time
        )
        self.market.receive_auction(new_auction)
    
    async def return_open_auction_params(self):
        """ Return open auction params to enable agents to place orders."""
        await self.check_step_done()
        return [auction["params"] for auction in self.market.get_open_auctions()]

    async def receive_order(self, agent, order, supply_time):
        await self.check_step_done()
            
        if self.market.receive_order(
            amount_kw=order.amount_kw,
            price_ct=order.price_ct,
            agent=agent,
            supply_time=supply_time,
            product_type="electricity"
        ):
            # TODO return confirmation
            pass
        else:
            # TODO return error
            pass
    
    async def return_awarded_orders(self, agent):
        await self.check_step_done()
            
        current_results = self.market.get_current_auction_results()
        relevant_results = {}
        # filter results for agent
        for auction_result in current_results:
            relevant_results[auction_result.params.supply_start_time] = {
                "order": [awarded_order for awarded_order in \
                    auction_result.awarded_orders if awarded_order.agent == agent],
                "clearing_price": auction_result.clearing_price
            }
        return relevant_results
    
    async def get_current_auction_results(self):
        """
        Returns current auction results based on product type and supply time
        """
        await self.check_step_done()
        return {f"{result.params.supply_start_time}_{result.params.product_type}": \
            result for result in self.market.get_current_auction_results()}
    
    def shutdown(self):
        try:
            self._main_loop.cancel()
        except:
            pass
