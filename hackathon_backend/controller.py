import asyncio
import traceback
import logging
import time, datetime
from typing import List
import pandas as pd
from pydantic.dataclasses import dataclass
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
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.config import Config, load_config

SIMULATION_TIME_SECONDS_PER_STEP = 900

logger = logging.getLogger(__name__)


class ControlException(Exception):
    def __init__(self, code, message, *args: object) -> None:
        super().__init__(*args)

        self.code = code
        self.message = message


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

    def __init__(self, config_file="config.json"):
        self.market = Market()
        self.unit_pool = UnitPool()
        self.registration_open = True
        self.current_market_task = asyncio.Future()
        self.current_market_task.set_result(None)
        self.current_unit_task = asyncio.Future()
        self.current_unit_task.set_result(None)
        self.registered = set()
        self.config_file = config_file
        self.config = load_config(self.config_file)
        self.step = 0
        self.actor_accounts = {}
        self.general_demand = None
        self.after_step_hooks = []
        self.remaining_sleep = 0
        self.actor_to_participant = {}

    def init(self):
        logger.info("Init controller...")
        self._main_loop = asyncio.create_task(self.initiate_stepping())

    def add_after_step_hook(self, hook):
        self.after_step_hooks.append(hook)

    async def _sleep_with_info_update(self, time_s):
        remaining_time = time_s
        self.remaining_sleep = remaining_time
        while remaining_time >= 1:
            await asyncio.sleep(1)
            remaining_time -= 1
            self.remaining_sleep = remaining_time
        if remaining_time > 0:
            await asyncio.sleep(remaining_time)
            self.remaining_sleep = 0

    async def initiate_stepping(self):
        try:
            self.config = load_config(self.config_file)
            await self._sleep_with_info_update(self.config.rt_step_init_delay_s)

            logger.info(f"Delay finished, starting the loop...")
            self.general_demand = create_general_demand("gd0")

            while True:
                self.config = load_config(self.config_file)
                while self.config.pause or self.step == self.config.max_steps:
                    self.config = load_config(self.config_file)
                    self.remaining_sleep = -1
                    await asyncio.sleep(1)
                    self.remaining_sleep = 0

                if not self.config.test_mode:
                    self.registration_open = False

                logger.info("Starting market task... %s", self.step)
                self.current_market_task = asyncio.create_task(
                    self.loop_market(self.step)
                )
                await self._sleep_with_info_update(self.config.rt_step_duration_s)
                self.current_unit_task = asyncio.create_task(self.loop_units(self.step))
                logger.info("Step finished... %s", self.step)
                self.step += 1

                for hook in self.after_step_hooks:
                    hook(self)
        except Exception as e:
            logger.exception("The main loop crashed!")

    async def loop_market(self, time_step):
        try:
            self.step_market(current_time=time_step * SIMULATION_TIME_SECONDS_PER_STEP)
            logger.info("Market stepped... %s", self.step)
        except Exception as e:
            logger.exception("The market-step %s crashed!", time_step)

    async def loop_units(self, time_step):
        try:
            self.step_units(current_time=time_step * SIMULATION_TIME_SECONDS_PER_STEP)
            logger.info("Units stepped... %s", self.step)
        except Exception as e:
            logger.exception("The unit-step %s crashed!", time_step)

    def get_current_simulation_time_unsafe(self):
        return self.step * SIMULATION_TIME_SECONDS_PER_STEP

    async def get_current_time(self):  # only for testing
        await self.check_market_step_done()
        await self.check_unit_step_done()
        return self.get_current_simulation_time_unsafe()

    def step_market(self, current_time):
        """Step market to next time interval.
        :param current_time: Current time in seconds (TODO align to time modelling)
        """
        market_inputs = MarketInputs()
        market_inputs._now_dt = datetime.datetime.fromtimestamp(
            current_time
        )  # TODO insert correct time
        step_size = 900
        market_inputs.step_size = step_size
        self.market.inputs = market_inputs

        tender_amount = self.general_demand.step(None, current_time // step_size).p_kw

        # insert new acution into market
        self.market.receive_auction(
            initiate_electricity_ask_auction(current_time, tender_amount=tender_amount)
        )
        self.market.step()

    def step_units(self, current_time):

        market_results = self.market.get_current_auction_results()
        auction_result = market_results.get(f"{current_time}_electricity", None)
        # accounter = None
        accounter = Accounter(auction_result=auction_result)
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
            logger.info("Stepped units of actor %s... %s", actor_id, actor_result)

            # store provided amount if bid was awarded
            provided_amount_kw += actor_result.p_kw

            payoff = accounter.calculate_payoff(actor_id, actor_result.p_kw)
            logger.info("Payoff for actor %s... %s", actor_id, payoff)

            # Award the correct accounts
            for i, agents in enumerate(accounter.return_awarded_agents(actor_id)):
                payoff_part = payoff * (
                    accounter.return_awarded(actor_id)[i] / setpoint
                )
                agent_key = str(agents)
                if len(agents) == 1:
                    agent_key = list(agents)[0]

                # store transaction in account
                if not agent_key in self.actor_accounts:
                    self.actor_accounts[agent_key] = Account()
                self.actor_accounts[agent_key].add_transaction(
                    awarded_amount=setpoint,
                    provided_power=actor_result.p_kw,
                    payoff=payoff_part,
                )

        self.general_demand.notify_supply(
            tender_amount_kw=tender_amount_kw, provided_amount_kw=provided_amount_kw
        )

    async def check_market_step_done(self):
        if not self.current_market_task.done():
            await self.current_market_task

    async def check_unit_step_done(self):
        if not self.current_unit_task.done():
            await self.current_unit_task

    def _part_to_actor_id(self, part_id):
        for a, p in self.actor_to_participant.items():
            if p == part_id:
                return a

    async def register_actor(self, participant_id: str) -> List[UnitInformation]:
        if self.registration_open:
            logger.info("Registering actor %s...", participant_id)

            if participant_id in self.config.participants:
                if participant_id in self.registered:
                    if self.config.test_mode:
                        aid = self._part_to_actor_id(participant_id)
                        return aid, self.unit_pool.read_units(aid)
                    raise ControlException(
                        400, "The participant is already registered!"
                    )
                self.registered.add(participant_id)
                logger.info("Registered actor %s...", participant_id)
            else:
                raise ControlException(403, "The requester is unknown!")

            actor_id, root_unit = allocate_default_actor_units()
            self.unit_pool.insert_actor_root(actor_id, root_unit)
            self.actor_accounts[actor_id] = Account()
            self.actor_to_participant[actor_id] = participant_id
            return actor_id, self.unit_pool.read_units(actor_id)
        else:
            raise ControlException(405, "Registration is closed!")

    async def read_units(self, actor_id) -> List[UnitInformation]:
        await self.check_unit_step_done()

        if not self.unit_pool.has_actor(actor_id):
            raise ControlException(404, "The actor id does not exist!")
        return self.unit_pool.read_units(actor_id)

    async def return_open_auction_params(self):
        """Return open auction params to enable actors to place orders."""
        await self.check_market_step_done()
        return [auction["params"] for auction in self.market.get_open_auctions()]

    async def return_auction_results(self):
        """Return open auction params to enable actors to place orders."""
        await self.check_market_step_done()
        return self.market.current_auction_results

    async def receive_order(self, actor_ids, amount_kw, price_ct, supply_time):
        """Receive order from actor and pass it to market.
        :param actor_id: Actor identifier
        :param order: Order object
        :param supply_time: Supply time of the auction (key to select auction)
        """
        await self.check_market_step_done()

        if self.market.receive_order(
            amount_kw=amount_kw,
            price_ct=price_ct,
            agents=actor_ids,
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
        await self.check_market_step_done()

        current_results = self.market.get_current_auction_results()
        relevant_results = {}
        # filter results for actor/agent
        for auction_result in current_results.values():
            relevant_results[auction_result.params.supply_start_time] = {
                "order": [
                    awarded_order
                    for awarded_order in auction_result.awarded_orders
                    if actor_id in awarded_order.agents
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
        await self.check_market_step_done()
        return self.market.get_current_auction_results()

    def get_balance_dict_sync(self):
        return {str(k): v.get_balance() for k, v in self.actor_accounts.items()}

    async def get_balance_dict(self):
        await self.check_market_step_done()
        return {str(k): v.get_balance() for k, v in self.actor_accounts.items()}

    async def get_gd_df(self) -> pd.DataFrame:
        await self.check_market_step_done()
        return self.general_demand.supply

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
