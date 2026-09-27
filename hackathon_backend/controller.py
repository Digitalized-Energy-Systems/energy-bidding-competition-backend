import asyncio
import math
import secrets
import traceback
import logging
import time, datetime
from typing import List, Optional
import numpy as np
import pandas as pd
from pydantic.dataclasses import dataclass
from .units.pool import UnitPool, allocate_default_actor_units
from .units.unit import UnitInput
from .units.weather import pv_irradiance_profile
from .market.market import Market, MarketInputs
from .market.auction import MAX_ORDERS_PER_AGENT, initiate_electricity_ask_auction
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
    plain_minimum_order_amount_kw,
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
from hackathon_backend.general_demand import create_general_demand, demand_per_actor_profile
from hackathon_backend.optimum import central_optimum
from hackathon_backend.config import Config, ParticipantEntry, load_config
from hackathon_backend.registration_keys import RegistrationKeys

SIMULATION_TIME_SECONDS_PER_STEP = 900
# an auction created at time t supplies at t + 4500 s (5 steps later), see
# initiate_electricity_ask_auction
AUCTION_SUPPLY_OFFSET_S = 4500
ELECTRICITY = "electricity"
# number of cooperative bids returned to the ui
UI_MAX_COOPERATIVE_BIDS = 50
AMOUNT_TOLERANCE_KW = 1e-9
MAX_PARTICIPANT_NAME_LENGTH = 32

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

    def __init__(
        self, config_file="config.json", registration_keys_file="registration_keys.json"
    ):
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
        # participant id -> name shown in the ranking
        self.participant_names = {}
        # one row per actor and unit step, for analysis (see plot.py)
        self.actor_history = []
        self._dispatch_cache = None
        self.cooperative_bids = CooperativeBidPool()
        self.registration_keys = RegistrationKeys(registration_keys_file)
        self.set_pv_seed(self.config.pv_seed)

    def set_pv_seed(self, pv_seed: Optional[int]):
        """Weather of the day: one PV profile shared by all actors, a new one
        every run unless the seed is configured (or restored on load)."""
        self.pv_seed = secrets.randbits(32) if pv_seed is None else pv_seed
        self.pv_profile = pv_irradiance_profile(self.pv_seed)

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

    def _reload_config(self):
        """Re-read the config file, keeping the last good config if it cannot
        be read (e.g. an editor saving it right now); stepping must never
        stop because of it."""
        try:
            self.config = load_config(self.config_file)
        except Exception as e:
            logger.warning("Keeping the last good config, %s is unreadable: %s", self.config_file, e)

    def _run_after_step_hooks(self):
        for hook in self.after_step_hooks:
            try:
                hook(self)
            except Exception:
                logger.exception("An after-step hook failed")

    async def initiate_stepping(self):
        try:
            logger.info(f"Delay finished, starting the loop...")
            self._reload_config()
            if self.general_demand is None:
                self.general_demand = create_general_demand("gd0")
            await self._sleep_with_info_update(self.config.rt_step_init_delay_s)

            while True:
                self._reload_config()
                while self.config.pause or self.step >= self.config.max_steps:
                    self._reload_config()
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
                # the hooks persist the state, which must include this settlement
                await self.check_unit_step_done()
                logger.info("Step finished... %s", self.step)
                self.step += 1

                self._run_after_step_hooks()
        except asyncio.CancelledError:
            raise
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

        # the demand per actor of the auction's supply interval
        general_demand_kw = demand_per_actor_profile(self.config.demand_per_actor_kw)[
            supply_step_from_time(current_time + AUCTION_SUPPLY_OFFSET_S)
        ]

        # the tender is the demand of the registered actors, in both modes
        # (see market/tender.py)
        tender_amount = round(max(1, len(self.registered)) * general_demand_kw, 1)
        if self.config.cooperative_bidding:
            minimum_order_amount_kw = cooperative_minimum_order_amount_kw(
                tender_amount, self.config.cooperative_minimum_order_kw
            )
        else:
            minimum_order_amount_kw = plain_minimum_order_amount_kw(
                tender_amount, self.config.plain_minimum_order_kw
            )

        # an auction whose supply lies after the last step would never be
        # settled
        supply_start_time = current_time + AUCTION_SUPPLY_OFFSET_S
        if supply_start_time < self.config.max_steps * SIMULATION_TIME_SECONDS_PER_STEP:
            self.market.receive_auction(
                initiate_electricity_ask_auction(
                    current_time,
                    tender_amount=tender_amount,
                    minimum_order_amount_kw=minimum_order_amount_kw,
                    maximum_price_ct=self.config.maximum_price_ct,
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
        accounter = Accounter(
            auction_result=auction_result,
            shortfall_penalty_ct_per_kw=self.config.shortfall_penalty_ct_per_kw,
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

            grid_penalty_ct = 0.0
            if actor_result.p_kw < 0:
                grid_penalty_ct = actor_result.p_kw * self.config.grid_penalty_ct_per_kw
                account.add_transaction(
                    awarded_amount=0,
                    provided_power=actor_result.p_kw,
                    payoff=grid_penalty_ct,
                )

            self.actor_history.append(
                {
                    "step": int(current_time // 900),
                    "actor_id": actor_id,
                    "awarded_kw": float(setpoint),
                    "delivered_kw": float(actor_result.p_kw),
                    **self.unit_pool.actor_to_root[actor_id].breakdown(),
                    "payoff_ct": float(payoff),
                    "grid_penalty_ct": float(grid_penalty_ct),
                    "balance_ct": float(account.get_balance()),
                }
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

    async def issue_registration_key(self, name: str, client_ip: Optional[str]) -> str:
        if not self.config.issue_registration_keys:
            raise ControlException(
                403, "Registration keys are handed out by the organizers!"
            )
        if not self.registration_open:
            raise ControlException(405, "Registration is closed!")
        if client_ip is None:
            raise ControlException(400, "The client address is unknown!")
        name = name.strip()
        if not 0 < len(name) <= MAX_PARTICIPANT_NAME_LENGTH or not name.isprintable():
            raise ControlException(
                400,
                f"The name must have 1 to {MAX_PARTICIPANT_NAME_LENGTH} printable "
                "characters!",
            )
        if self.registration_keys.count_for_ip(client_ip) >= self.config.max_keys_per_ip:
            raise ControlException(
                409, "A registration key has already been issued to your address!"
            )
        taken_names = self.registration_keys.names() + [
            configured_name for _, _, configured_name in self._configured_participants()
        ]
        if name.casefold() in {taken.casefold() for taken in taken_names}:
            raise ControlException(409, "The name is already taken!")

        key = self.registration_keys.issue(name, client_ip)
        logger.info("Issued a registration key for %s to %s", name, client_ip)
        return key

    def _configured_participants(self):
        """(participant id, key, display name) of the participants in the
        config file."""
        for entry in self.config.participants:
            if isinstance(entry, ParticipantEntry):
                yield f"config:{entry.name}", entry.key, entry.name
            else:
                # a legacy id is its own key and is shown without its last
                # two characters
                yield entry, entry, entry[0:-2]

    def _resolve_participant(self, key: str):
        """(participant id, display name) of a registration key, or None."""
        for participant_id, participant_key, name in self._configured_participants():
            if secrets.compare_digest(key.encode(), participant_key.encode()):
                return participant_id, name
        issued = self.registration_keys.get(key)
        if issued is not None:
            return issued.participant_id, issued.name
        return None

    def _authenticate(self, actor_id, key):
        """The actor id is public: acting for an actor or reading its private
        state requires the registration key the actor registered with."""
        self._check_actor_exists(actor_id)
        resolved = None if key is None else self._resolve_participant(key)
        if resolved is None or self.actor_to_participant.get(actor_id) != resolved[0]:
            raise ControlException(403, "The key does not belong to the actor!")

    def participant_display_names(self):
        """actor id -> name shown in the ranking (no key material)."""
        return {
            actor_id: self.participant_names.get(participant_id, participant_id[0:-2])
            for actor_id, participant_id in self.actor_to_participant.items()
        }

    def check_admin_token(self, token: Optional[str]):
        if self.config.admin_token is None:
            raise ControlException(
                403, "The admin endpoints are disabled, configure an admin_token!"
            )
        if token is None or not secrets.compare_digest(
            token.encode(), self.config.admin_token.encode()
        ):
            raise ControlException(403, "The admin token is wrong!")

    async def register_actor(self, key: str) -> List[UnitInformation]:
        if self.registration_open:
            resolved = self._resolve_participant(key)
            if resolved is None:
                raise ControlException(403, "The requester is unknown!")
            participant_id, name = resolved
            logger.info("Registering actor %s...", participant_id)

            if participant_id in self.registered:
                if self.config.test_mode:
                    aid = self._part_to_actor_id(participant_id)
                    return aid, self.unit_pool.read_units(aid)
                raise ControlException(400, "The participant is already registered!")
            self.registered.add(participant_id)
            logger.info("Registered actor %s...", participant_id)

            actor_id, root_unit = allocate_default_actor_units(
                demand_size=self.config.actor_load_kw,
                load_profile_kw=self.config.household_load_kw,
                pv_profile=self.pv_profile,
                pv_peak_kw=self.config.pv_peak_kw,
                forecast_seed=self.pv_seed,
                battery_capacity_kwh=self.config.battery_capacity_kwh,
                battery_charge_max_kw=self.config.battery_charge_max_kw,
                battery_discharge_max_kw=self.config.battery_discharge_max_kw,
                battery_initial_soc_percent=self.config.battery_initial_soc_percent,
            )
            self.unit_pool.insert_actor_root(actor_id, root_unit)
            self.actor_accounts[actor_id] = Account()
            self.actor_to_participant[actor_id] = participant_id
            self.participant_names[participant_id] = name
            return actor_id, self.unit_pool.read_units(actor_id)
        else:
            raise ControlException(405, "Registration is closed!")

    async def read_units(self, actor_id, key) -> List[UnitInformation]:
        await self.check_unit_step_done()
        self._authenticate(actor_id, key)
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

    def _check_order_slots(self, actor_ids, auction):
        """An actor takes part in at most MAX_ORDERS_PER_AGENT orders of an
        auction, and every open cooperative bid it is a member of reserves
        one of them, so a filled cooperative bid can always be placed."""
        for actor_id in actor_ids:
            orders = sum(
                1 for order in auction.order_container.orders if actor_id in order.agents
            )
            if (
                orders + self.cooperative_bids.open_memberships(actor_id, auction.id)
                >= MAX_ORDERS_PER_AGENT
            ):
                raise ControlException(
                    400,
                    f"An actor may take part in at most {MAX_ORDERS_PER_AGENT} "
                    "orders of one auction, open cooperative bids included!",
                )

    async def receive_order(self, actor_id, key, amount_kw, price_ct, supply_time):
        """Place an order of one actor in the auction of the supply time.
        Actors pool their power only through cooperative bids (see
        _place_cooperative_order), which every member joins itself.
        """
        await self.check_market_step_done()
        self._authenticate(actor_id, key)
        auction = self.market.auctions.get(
            self.market._get_auction_id_from_supply_time_and_product_type(
                supply_time, ELECTRICITY
            )
        )
        if auction is not None:
            self._check_order_slots([actor_id], auction)
        try:
            ok = self.market.receive_order(
                amount_kw=[amount_kw],
                price_ct=price_ct,
                agents=[actor_id],
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

    async def return_awarded_orders(self, actor_id, key):
        """Return awarded orders for actor.
        :param actor_id: Actor identifier
        """
        await self.check_market_step_done()
        self._authenticate(actor_id, key)

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
        self, actor_id, key, amount_kw, price_ct, supply_time, target_amount_kw=None
    ) -> CooperativeBid:
        """Open a cooperative bid for the auction supplying at supply_time
        with the proposer as first member.
        :param actor_id: Proposing actor
        :param key: Registration key of the proposing actor
        :param amount_kw: Amount the proposer puts into the bid
        :param price_ct: Common price of the whole bid
        :param supply_time: Supply time of the auction (key to select auction)
        :param target_amount_kw: Target of the bid, default is the minimum
        order amount of the auction
        """
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._authenticate(actor_id, key)
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
        self._check_order_slots([actor_id], auction)

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

    async def join_cooperative_bid(self, actor_id, key, cooperative_bid_id, amount_kw):
        """Join an open cooperative bid, or top up the own amount. When the
        target is filled the group order is placed in the market immediately.
        :return: (bid, accepted_amount_kw)
        """
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._authenticate(actor_id, key)
        bid = self.cooperative_bids.get(cooperative_bid_id)
        if bid is None:
            raise ControlException(404, "The cooperative bid does not exist!")
        self._check_finite(amount_kw=amount_kw)
        auction = self.market.auctions.get(bid.auction_id)
        if auction is not None and not bid.has_member(actor_id):
            self._check_order_slots([actor_id], auction)

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

    async def withdraw_cooperative_bid(self, actor_id, key, cooperative_bid_id):
        """Leave an open cooperative bid; the proposer cancels it."""
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._authenticate(actor_id, key)
        try:
            return self.cooperative_bids.withdraw(actor_id, cooperative_bid_id)
        except CooperativeBidError as e:
            raise ControlException(e.code, e.message)

    async def return_cooperative_bids_of_actor(self, actor_id, key):
        """Cooperative bids of every status in which the actor is a member."""
        await self.check_market_step_done()
        self._check_cooperative_bidding_enabled()
        self._authenticate(actor_id, key)
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

    def status(self):
        return {
            "step": self.step,
            "max_steps": self.config.max_steps,
            "finished": self.step >= self.config.max_steps,
        }

    async def system_dispatch(self):
        """The actual dispatch of all teams so far next to the central
        optimum of the same steps (see optimum.py), with the energy balance
        of both: start charge + PV - own loads + grid draw - market demand
        served - lost = left in the batteries. Lost is PV surplus that no
        battery stored and nobody bought (for the teams: power delivered
        beyond their awards). Solved once per step."""
        await self.check_unit_step_done()
        key = (self.step, len(self.actor_history))
        if self._dispatch_cache is not None and self._dispatch_cache[0] == key:
            return self._dispatch_cache[1]
        dispatch = self._system_dispatch()
        self._dispatch_cache = (key, dispatch)
        return dispatch

    def _system_dispatch(self):
        status = self.status()
        if not self.actor_history or self.general_demand is None:
            return {**status, "steps": 0}
        n_steps = max(row["step"] for row in self.actor_history) + 1
        teams = list(dict.fromkeys(row["actor_id"] for row in self.actor_history))
        index = {actor_id: i for i, actor_id in enumerate(teams)}
        shape = (len(teams), n_steps)
        columns = ("pv_kw", "load_kw", "awarded_kw", "delivered_kw", "battery_kw", "soc_percent")
        rows = {name: np.zeros(shape) for name in columns}
        active = np.zeros(shape, dtype=bool)
        for row in self.actor_history:
            i, t = index[row["actor_id"]], row["step"]
            active[i, t] = True
            for name in columns:
                rows[name][i, t] = row[name] or 0.0

        cap_kwh, charge_kw, discharge_kw = np.zeros(len(teams)), np.zeros(len(teams)), np.zeros(len(teams))
        for actor_id, i in index.items():
            for unit in self.unit_pool.read_units(actor_id):
                if hasattr(unit, "cap_kwh"):
                    cap_kwh[i] += unit.cap_kwh
                    charge_kw[i] += unit.p_charge_max_kw
                    discharge_kw[i] += unit.p_discharge_max_kw
        # the charge before each team's first step, from the history rather
        # than the config, which may have changed since the start of the day
        first = active.argmax(axis=1)
        teams_idx = np.arange(len(teams))
        initial_kwh = (
            rows["soc_percent"][teams_idx, first] / 100 * cap_kwh
            - rows["battery_kw"][teams_idx, first] * 0.25
        )

        supply = self.general_demand.supply.sort_index()
        demand = np.zeros(n_steps)
        served = np.zeros(n_steps)
        for t, (tender, provided) in enumerate(
            zip(supply["tender_amount_kw"].astype(float), supply["provided_amount_kw"].astype(float))
        ):
            if t < n_steps:
                demand[t], served[t] = tender, provided

        delivered = rows["delivered_kw"]
        actual = {
            "served_kw": served,
            "unserved_kw": np.maximum(demand - served, 0.0),
            "grid_kw": np.maximum(-delivered, 0.0).sum(axis=0),
            "lost_kw": np.maximum(delivered - np.maximum(rows["awarded_kw"], 0.0), 0.0).sum(axis=0),
            "battery_kw": rows["battery_kw"].sum(axis=0),
            "stored_kwh": (rows["soc_percent"] / 100 * cap_kwh[:, None]).sum(axis=0),
        }
        optimum = central_optimum(
            rows["pv_kw"], rows["load_kw"], active, cap_kwh, charge_kw, discharge_kw, initial_kwh, demand
        )

        def kwh(values):
            return round(float(np.sum(values)) * 0.25, 3)

        def summary(result):
            return {
                **{name: [round(float(v), 4) for v in values] for name, values in result.items()},
                "served_kwh": kwh(result["served_kw"]),
                "unserved_kwh": kwh(result["unserved_kw"]),
                "grid_kwh": kwh(result["grid_kw"]),
                "lost_kwh": kwh(result["lost_kw"]),
                "stored_end_kwh": round(float(result["stored_kwh"][-1]), 3),
            }

        return {
            **status,
            "steps": n_steps,
            "teams": len(teams),
            "demand_kw": [round(float(v), 4) for v in demand],
            "demand_kwh": kwh(demand),
            "capacity_kwh": round(float(cap_kwh.sum()), 3),
            "initial_kwh": round(float(initial_kwh.sum()), 3),
            "pv_kwh": kwh(rows["pv_kw"]),
            "load_kwh": kwh(rows["load_kw"]),
            "actual": summary(actual),
            "optimum": summary(optimum),
        }

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
