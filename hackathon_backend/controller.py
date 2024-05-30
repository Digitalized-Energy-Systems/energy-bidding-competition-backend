import asyncio
import time, datetime, json
from typing import List
from .units.pool import UnitPool, allocate_default_actor_units
from .units.unit import UnitInput
from .market.market import Market, MarketInputs
from .market.auction import initiate_electricity_ask_auction
from hackathon_backend.units.pool import (
    UnitInformation,
    UnitPool,
    allocate_default_actor_units,
)
from hackathon_backend.accounting.accounter import (
    ElectricityAskAuctionAccounter as Accounter,
)
from hackathon_backend.accounting.account import Account
from hackathon_backend.general_demand import GeneralDemand, create_general_demand

DEFAULT_CONFIG_FILE = "config.json"


class ControlException(Exception):
    def __init__(self, code, message, *args: object) -> None:
        super().__init__(*args)

        self.code = code
        self.message = message


class Config:
    participants: List[str]
    rt_step_duration_s: float
    rt_step_init_delay_s: float
    pause: bool

    def __init__(self, payload) -> None:
        self.__dict__ = payload


def load_config(config_file) -> Config:
    with open(config_file) as f:
        return Config(json.load(f))


def load_default_config() -> Config:
    return load_config(DEFAULT_CONFIG_FILE)


class Controller:
    """
    Needed functionality:
    - actor management
      - accept actor registration
      - store actor identifiers
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
      - filter according to asking actor/agent
      - return full results for gui
    """

    def __init__(self, config: Config = None):
        self.market = Market()
        self.unit_pool = UnitPool()
        self.current_task = asyncio.Future()
        self.current_task.set_result(None)
        self.registered = set()
        self.config = load_default_config() if config is None else config
        self.step = 0
        self.actor_accounts = {}
        self.general_demand = None

    def init(self):
        self._main_loop = asyncio.create_task(self.initiate_stepping())

    async def initiate_stepping(self):
        await asyncio.sleep(self.config.rt_step_init_delay_s)
        self.general_demand = create_general_demand(len(self.actor_accounts))

        while True:
            while self.config.pause:
                asyncio.sleep(1)

            await asyncio.sleep(self.config.rt_step_duration_s)
            self.current_task = asyncio.create_task(self.loop(self.step))
            self.step += 1

    async def loop(self, time_step):
        # TODO step 15 minutes ahead
        current_time = time.time()
        current_time = time_step * 900

        self.current_time = current_time  # only for testing

        self.step_market(current_time=current_time)
        self.step_units(current_time=current_time)
        print(f"Step finished...{current_time}")

    async def get_current_time(self):  # only for testing
        await self.check_step_done()
        return self.current_time

    def step_market(self, current_time):
        """Step market to next time interval.
        :param current_time: Current time in seconds (TODO align to time modelling)
        """
        print(f"Step Market {current_time}...")
        market_inputs = MarketInputs()
        market_inputs._now_dt = datetime.datetime.fromtimestamp(
            current_time
        )  # TODO insert correct time
        step_size = 900
        market_inputs.step_size = step_size
        self.market.inputs = market_inputs
        
        tender_amount = self.general_demand.step(None, current_time//step_size).p_kw
        # insert new acution into market
        self.market.receive_auction(
            initiate_electricity_ask_auction(current_time, tender_amount=tender_amount)
        )
        self.market.step()

    def step_units(self, current_time):
        print(f"Step Units {current_time}...")

        market_results = self.market.get_current_auction_results()
        auction_result = market_results.get(f"{current_time}_electricity", None)
        # accounter = None
        accounter = Accounter(
            auction_result=auction_result
        )
        # retrieve tender_amount
        if auction_result is not None:
            tender_amount_kw = auction_result.params.tender_amount_kw
        else:
            tender_amount_kw = 0
        provided_amount_kw = 0

        for actor_id in self.unit_pool.actor_to_root.keys():
            # retrieve setpoint from awarded orders
            setpoint = accounter.return_awarded_sum(actor_id)

            # step actor
            actor_result = self.unit_pool.step_actor(
                uuid=actor_id,
                input=UnitInput(
                    delta_t=900,
                    p_kw=setpoint,
                    q_kvar=0,
                ),
                step=current_time // 900,
                other_inputs=[],
            )
            print(f"Stepped units of actor {actor_id}... {actor_result}")

            payoff = accounter.calculate_payoff(actor_id, actor_result.p_kw)
            # store transaction in account
            self.actor_accounts[actor_id].add_transaction(
                awarded_amount=setpoint, provided_power=actor_result.p_kw, payoff=payoff
            )
            print(f"Payoff for actor {actor_id}... {payoff}")
            
            # store provided amount if bid was awarded
            if setpoint > 0:
                provided_amount_kw += actor_result.p_kw
        self.general_demand.notify_supply(
            tender_amount_kw=tender_amount_kw, 
            provided_amount_kw=provided_amount_kw
        )

    async def check_step_done(self):
        if not self.current_task.done():
            await self.current_task

    async def register_actor(self, participant_id: str) -> List[UnitInformation]:
        await self.check_step_done()
        print(f"Registering actor {participant_id}...")

        if participant_id in self.config.participants:
            if participant_id in self.registered:
                raise ControlException(400, "The participant is already registered!")
            self.registered.add(participant_id)
            print(f"Registered actor {participant_id}...")
        else:
            raise ControlException(403, "The requester is unknown!")

        actor_id, root_unit = allocate_default_actor_units()
        self.unit_pool.insert_actor_root(actor_id, root_unit)
        self.actor_accounts[actor_id] = Account()
        return actor_id, self.unit_pool.read_units(actor_id)

    async def read_units(self, actor_id) -> List[UnitInformation]:
        await self.check_step_done()

        if not self.unit_pool.has_actor(actor_id):
            raise ControlException(404, "The actor id does not exist!")
        return self.unit_pool.read_units(actor_id)

    async def return_open_auction_params(self):
        """Return open auction params to enable actors to place orders."""
        await self.check_step_done()
        return [auction["params"] for auction in self.market.get_open_auctions()]

    async def receive_order(self, actor_id, amount_kw, price_ct, supply_time):
        """Receive order from actor and pass it to market.
        :param actor_id: Actor identifier
        :param order: Order object
        :param supply_time: Supply time of the auction (key to select auction)
        """
        await self.check_step_done()

        if self.market.receive_order(
            amount_kw=amount_kw,
            price_ct=price_ct,
            agent=actor_id,
            supply_time=supply_time,
            product_type="electricity",
        ):
            return True
        else:
            raise ControlException(404, "The specified auction does not exist!")

    async def return_awarded_orders(self, actor_id):
        """Return awarded orders for actor.
        :param actor_id: Actor identifier
        """
        await self.check_step_done()

        current_results = self.market.get_current_auction_results()
        relevant_results = {}
        # filter results for actor/agent
        for auction_result in current_results.values():
            relevant_results[auction_result.params.supply_start_time] = {
                "order": [
                    awarded_order
                    for awarded_order in auction_result.awarded_orders
                    if awarded_order.agent == actor_id
                ],
                "clearing_price": auction_result.clearing_price,
            }
            # TODO Each "order" contains an "auction_id", which the actors 
            # do not need to receive
        return relevant_results

    async def get_current_auction_results(self):
        """
        Returns current auction results based on product type and supply time
        """
        await self.check_step_done()
        return self.market.get_current_auction_results()

    def reset(self):
        self.market.reset()

        return {
            f"{result.params.supply_start_time}_{result.params.product_type}": result
            for result in self.market.get_current_auction_results()
        }

    def shutdown(self):
        try:
            self._main_loop.cancel()
        except:
            pass
