import asyncio
import math
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
from .market.cooperative import (
    CooperativeBid,
    CooperativeBidError,
    CooperativeBidPool,
    STATUS_CLOSED,
    STATUS_EXPIRED,
)
from .market.tender import (
    supply_step_from_time,
    cooperative_minimum_order_amount_kw,
    cooperative_tender_amount_kw,
)
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
# an auction created at time t supplies at t + 4500 s (5 steps later), see
# initiate_electricity_ask_auction
AUCTION_SUPPLY_OFFSET_S = 4500
ELECTRICITY = "electricity"
# number of cooperative bids returned to the ui
UI_MAX_COOPERATIVE_BIDS = 50
AMOUNT_TOLERANCE_KW = 1e-9

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
        self.cooperative_bids = CooperativeBidPool()

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
            logger.info(f"Delay finished, starting the loop...")
            self.config = load_config(self.config_file)
            self.general_demand = create_general_demand("gd0")
            await self._sleep_with_info_update(self.config.rt_step_init_delay_s)

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
        market_inputs._now_dt = current_time  # TODO insert correct time
        step_size = 900
        market_inputs.step_size = step_size
        self.market.inputs = market_inputs

        # the general demand is stepped in every mode (it tracks its time step)
        general_demand_kw = self.general_demand.step(
            None, current_time // step_size
        ).p_kw

        # the tender scales with the registered actors in both modes, so it
        # fits the generation and flexibility they bring
        demand_tender_kw = round(
            max(1, len(self.registered)) * general_demand_kw,
            1,
        )
        if self.config.cooperative_bidding:
            # cooperative bidding: the minimum order amount follows the
            # profile of market/tender.py, which a single actor can only
            # reach alone for about half of the day; the tender is the plain
            # tender floored at minimum / share, so the minimum is never more
            # than that share of the tender
            supply_step = supply_step_from_time(current_time + AUCTION_SUPPLY_OFFSET_S)
            minimum_order_amount_kw = cooperative_minimum_order_amount_kw(supply_step)
            tender_amount = cooperative_tender_amount_kw(
                supply_step, demand_tender_kw, self.config.cooperative_min_order_share
            )
        else:
            minimum_order_amount_kw = 1.0
            tender_amount = demand_tender_kw

        # insert new acution into market
        self.market.receive_auction(
            initiate_electricity_ask_auction(
                current_time,
                tender_amount=tender_amount,
                minimum_order_amount_kw=minimum_order_amount_kw,
            )
        )
        self.market.step()

        # cooperative bids which did not reach their target until the gate
        # closure of their auction are never submitted
        self.cooperative_bids.expire_for_closed_auctions(
            [auction.id for auction in self.market.open_auctions]
        )

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
            provided_amount_kw += max(min(actor_result.p_kw, setpoint), 0)
            logger.info(
                "provided_amount_kw for actor %s... %s", actor_id, provided_amount_kw
            )

            # The accounter only covers the actor's OWN awarded shares at the
            # order prices, so the value of a group order (several agents)
            # is naturally split between its members by the amount each
            # member put into the order. Every member is credited on its own
            # account; there is no joint account.
            payoff = accounter.calculate_payoff(actor_id, actor_result.p_kw)
            logger.info("Payoff for actor %s... %s", actor_id, payoff)

            # store transaction in the actor's own account (only actors with
            # an awarded order get a row, as before the unification)
            if actor_id not in self.actor_accounts:
                self.actor_accounts[actor_id] = Account()
            account = self.actor_accounts[actor_id]
            if setpoint > 0:
                account.add_transaction(
                    awarded_amount=setpoint,
                    provided_power=actor_result.p_kw,
                    payoff=payoff,
                )

            penalty_ct = 1000 / 4  # 30 ct per quarterhour
            if actor_result.p_kw < 0:
                account.add_transaction(
                    awarded_amount=0,
                    provided_power=actor_result.p_kw,
                    payoff=actor_result.p_kw * penalty_ct,
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


    async def return_price_history(self):
        """Return open auction params to enable actors to place orders."""
        await self.check_market_step_done()
        return [auction.result.clearing_price for auction in self.market.expired_auctions]


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
        if len(actor_ids) > 1 and self.config.cooperative_bidding:
            # a group order books payoff and penalties on every listed
            # actor's own account; while cooperative bidding is enabled only
            # a cooperative bid, which every member joins itself, may create
            # one (see _place_cooperative_order)
            raise ControlException(
                403,
                "Group orders are placed through cooperative bids while "
                "cooperative bidding is enabled, see /market/cooperative/propose "
                "and /market/cooperative/join",
            )
        try:
            ok = self.market.receive_order(
                amount_kw=amount_kw,
                price_ct=price_ct,
                agents=actor_ids,
                supply_time=supply_time,
                product_type="electricity",
            )
        except Exception as e:
            raise ControlException(400, str(e))

        # TODO move exception creation to the market
        if ok:
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

    # ---- cooperative bidding ----

    def _check_cooperative_bidding_enabled(self):
        # self.config is re-read from the config file every step, so the
        # toggle can be flipped at runtime
        if not self.config.cooperative_bidding:
            raise ControlException(403, "Cooperative bidding is disabled")

    def _check_actor_exists(self, actor_id):
        if not self.unit_pool.has_actor(actor_id):
            raise ControlException(404, "The actor id does not exist!")

    @staticmethod
    def _check_finite(**values):
        """NaN passes every comparison based check (and is not JSON
        serializable), so it is refused before any state change."""
        for name, value in values.items():
            if value is not None and not math.isfinite(value):
                raise ControlException(400, f"The {name} must be a finite number!")

    def _find_open_auction(self, supply_time, product_type=ELECTRICITY):
        auction_id = self.market._get_auction_id_from_supply_time_and_product_type(
            supply_time, product_type
        )
        auction = self.market.auctions.get(auction_id, None)
        if auction is None or auction.status != "open":
            raise ControlException(
                404, "There is no open auction for the specified supply time!"
            )
        return auction

    async def propose_cooperative_bid(
        self, actor_id, amount_kw, price_ct, supply_time, target_amount_kw=None
    ) -> CooperativeBid:
        """Open a cooperative bid for the auction supplying at supply_time
        with the proposer as first member.
        :param actor_id: Proposing actor
        :param amount_kw: Amount the proposer puts into the bid
        :param price_ct: Common price of the whole bid
        :param supply_time: Supply time of the auction (key to select auction)
        :param target_amount_kw: Target of the bid, default is the minimum
        order amount of the auction
        """
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._check_actor_exists(actor_id)
        auction = self._find_open_auction(supply_time)
        params = auction.params

        self._check_finite(
            amount_kw=amount_kw, price_ct=price_ct, target_amount_kw=target_amount_kw
        )
        if amount_kw <= 0:
            raise ControlException(400, "The amount_kw must be greater than 0!")
        # limit order price like the auction does
        price_ct = min(price_ct, params.maximum_price_ct)
        if target_amount_kw is None:
            target_amount_kw = params.minimum_order_amount_kw
        if (
            target_amount_kw < params.minimum_order_amount_kw - AMOUNT_TOLERANCE_KW
            or target_amount_kw > params.tender_amount_kw + AMOUNT_TOLERANCE_KW
        ):
            raise ControlException(
                400,
                "The target_amount_kw must be between the minimum order amount "
                f"({params.minimum_order_amount_kw} kW) and the tender amount "
                f"({params.tender_amount_kw} kW) of the auction!",
            )

        try:
            return self.cooperative_bids.propose(
                actor_id=actor_id,
                amount_kw=amount_kw,
                price_ct=price_ct,
                auction_id=auction.id,
                supply_time=params.supply_start_time,
                target_amount_kw=target_amount_kw,
                created_time=self.get_current_simulation_time_unsafe(),
                product_type=params.product_type,
            )
        except CooperativeBidError as e:
            raise ControlException(e.code, e.message)

    async def join_cooperative_bid(self, actor_id, cooperative_bid_id, amount_kw):
        """Join an open cooperative bid. When the target is filled the
        group order is placed in the market immediately.
        :return: (bid, accepted_amount_kw)
        """
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._check_actor_exists(actor_id)
        if self.cooperative_bids.get(cooperative_bid_id) is None:
            raise ControlException(404, "The cooperative bid does not exist!")
        self._check_finite(amount_kw=amount_kw)

        try:
            bid, accepted_amount_kw = self.cooperative_bids.join(
                actor_id, cooperative_bid_id, amount_kw
            )
        except CooperativeBidError as e:
            raise ControlException(e.code, e.message)

        if bid.status == STATUS_CLOSED and not bid.order_placed:
            self._place_cooperative_order(bid)
        return bid, accepted_amount_kw

    def _place_cooperative_order(self, bid: CooperativeBid):
        """Place the group order of a filled cooperative bid: one order
        with all members as agents and their amounts, at the common price.
        Every member is later credited on its own account by its awarded
        share."""
        try:
            ok = self.market.receive_order(
                amount_kw=bid.member_amounts_kw,
                price_ct=bid.price_ct,
                agents=bid.actor_ids,
                supply_time=bid.supply_time,
                product_type=bid.product_type,
                auction_id=bid.auction_id,
            )
        except Exception as e:
            bid.status = STATUS_EXPIRED
            logger.warning("Cooperative bid %s could not be placed: %s", bid.id, e)
            raise ControlException(
                409, f"The cooperative bid could not be placed, it expired: {e}"
            )
        if not ok:
            bid.status = STATUS_EXPIRED
            raise ControlException(
                409, "The cooperative bid could not be placed, it expired!"
            )
        bid.order_placed = True
        logger.info("Cooperative bid %s placed as group order", bid.id)

    async def return_open_cooperative_bids(self, supply_time=None):
        """Open cooperative bids which actors can join."""
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        return self.cooperative_bids.open_bids(supply_time=supply_time)

    async def return_cooperative_bids_of_actor(self, actor_id):
        """Cooperative bids of every status in which the actor is a member."""
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._check_actor_exists(actor_id)
        return self.cooperative_bids.bids_for_actor(actor_id)

    async def return_all_cooperative_bids(self):
        """All cooperative bids for the ui, newest first (at most
        UI_MAX_COOPERATIVE_BIDS). Does not check the toggle."""
        await self.check_market_step_done()
        bids = sorted(
            self.cooperative_bids.all_bids(),
            key=lambda bid: bid.created_time,
            reverse=True,
        )
        return bids[:UI_MAX_COOPERATIVE_BIDS]

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
