import json
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional, Union

from hackathon_backend.accounting.accounter import PENALTY_CT_PER_KW
from hackathon_backend.market.auction import MAXIMUM_PRICE_CT
from hackathon_backend.market.tender import (
    COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW,
    PLAIN_MINIMUM_ORDER_AMOUNT_KW,
)
from hackathon_backend.profiles import DEMAND_PER_ACTOR_KW, HOUSEHOLD_LOAD_KW
from hackathon_backend.units.battery import BATTERY_CAPACITY_KWH, BATTERY_POWER_KW
from hackathon_backend.units.pv import P_PV_PEAK


DEFAULT_CONFIG_FILE = "config.json"


class ParticipantEntry(BaseModel):
    """A participant configured by the organizers: the name is shown in the
    ranking, the key is secret and used to register and to authenticate."""

    name: str
    key: str


# above the price cap, so selling the energy for the own load at any price
# and drawing it from the grid again never pays
GRID_PENALTY_CT_PER_KW = 1500

# one value per full hour of the day, linear in between
HourlyKw = Annotated[List[Annotated[float, Field(ge=0)]], Field(min_length=24, max_length=24)]


class Config(BaseModel):
    # a plain string is a legacy participant id which is also its key; it is
    # shown without its last two characters, so all but those two characters
    # are public. Prefer {"name", "key"} entries or issued registration keys.
    participants: List[Union[str, ParticipantEntry]] = []
    rt_step_duration_s: float
    rt_step_init_delay_s: float
    pause: bool
    max_steps: int
    test_mode: bool
    # cooperative bidding: the minimum order amount (1.8 kW) cannot be
    # reached by a single actor at night (see
    # hackathon_backend/market/tender.py); actors can pool their power in a
    # cooperative bid (see hackathon_backend/market/cooperative.py)
    cooperative_bidding: bool = False
    # net power drawn from the grid, per kW and quarter-hour; keep it above
    # maximum_price_ct (see GRID_PENALTY_CT_PER_KW)
    grid_penalty_ct_per_kw: float = Field(default=GRID_PENALTY_CT_PER_KW, ge=0)
    # seed of the PV weather of the day; None draws a new day every run
    pv_seed: Optional[int] = None
    # charge of every battery at the start of the day (see
    # hackathon_backend/profiles.py for how it shapes the day)
    battery_initial_soc_percent: float = Field(default=65, ge=0, le=100)
    # constant own load of every actor in kW; None is the household profile
    # of hackathon_backend/profiles.py (morning and evening peak)
    actor_load_kw: Optional[float] = Field(default=None, gt=0)

    # the day (see hackathon_backend/profiles.py); the units are built when
    # an actor registers, the market values apply to every new auction
    # own load of every actor in kW, unless actor_load_kw is set
    household_load_kw: HourlyKw = Field(default_factory=lambda: list(HOUSEHOLD_LOAD_KW))
    # market demand per registered actor in kW; the tender of an auction is
    # the number of registered actors times this, rounded to 0.1 kW
    demand_per_actor_kw: HourlyKw = Field(default_factory=lambda: list(DEMAND_PER_ACTOR_KW))
    pv_peak_kw: float = Field(default=P_PV_PEAK, gt=0)
    battery_capacity_kwh: float = Field(default=BATTERY_CAPACITY_KWH, gt=0)
    battery_charge_max_kw: float = Field(default=BATTERY_POWER_KW, gt=0)
    battery_discharge_max_kw: float = Field(default=BATTERY_POWER_KW, gt=0)

    # market rules
    maximum_price_ct: float = Field(default=MAXIMUM_PRICE_CT, gt=0)
    # awarded but not delivered power costs the order price, but at least
    # this, per kW and quarter-hour
    shortfall_penalty_ct_per_kw: float = Field(default=PENALTY_CT_PER_KW, ge=0)
    # minimum order amount of the cooperative mode and of the plain mode
    # (0.1 kW, the resolution of the tender: no real minimum); both never
    # more than the tender
    cooperative_minimum_order_kw: float = Field(default=COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW, gt=0)
    plain_minimum_order_kw: float = Field(default=PLAIN_MINIMUM_ORDER_AMOUNT_KW, gt=0)
    # token for the admin endpoints (/admin/*); they are disabled while no
    # token is configured
    admin_token: Optional[str] = None
    issue_registration_keys: bool = False
    max_keys_per_ip: int = Field(default=1, ge=1)


def load_config(config_file) -> Config:
    with open(config_file) as f:
        return Config.model_validate_json(f.read())


def load_default_config() -> Config:
    return load_config(DEFAULT_CONFIG_FILE)
