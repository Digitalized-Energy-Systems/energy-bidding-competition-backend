from uuid import UUID, uuid4
from typing import Dict, List
import logging

from .unit import Unit, UnitInput, UnitInformation
from .vpp import VPP, VPPInformation
from .load import create_demand
from .battery import BATTERY_CAPACITY_KWH, BATTERY_POWER_KW, create_battery
from .pv import P_PV_PEAK, create_pv_unit
from .weather import clear_sky_profile
from hackathon_backend.profiles import HOUSEHOLD_LOAD_KW, hourly_to_steps


logger = logging.getLogger(__name__)


def _flatten_unit_information(unit_information):
    """Flatten information from unit tree to list of unit information."""
    if type(unit_information) == VPPInformation:
        all_information = []
        for sub_ui in unit_information.unit_information_list:
            f_sub_ui = _flatten_unit_information(sub_ui)
            all_information += f_sub_ui
        return all_information
    else:
        return [unit_information]


class UnitPool:
    """Container to store units which are organized in a tree structure.
    They belong to actors identified by UUIDs and their information is
    returned as a list of unit information."""

    actor_to_root: Dict[str, Unit]

    def __init__(self) -> None:
        self.actor_to_root = {}

    def step_actor(
        self,
        uuid: str,
        input: UnitInput,
        step: int,
        other_inputs: Dict[str, UnitInput] = None,
    ):
        logger.info("Step actor %s...", uuid)
        return self.actor_to_root[uuid].step(input, step, other_inputs=other_inputs)

    def insert_actor_root(self, actor: str, unit_root: Unit):
        self.actor_to_root[actor] = unit_root

    def has_actor(self, actor_id: str):
        return actor_id in self.actor_to_root

    def read_units(self, actor: str) -> List[UnitInformation]:
        """Initiate reading of unit information and flatten it."""
        root = self.actor_to_root[actor]
        unit_information = root.read_information()
        return _flatten_unit_information(unit_information)


def allocate_default_actor_units(
    demand_size=None,
    pv_profile=None,
    forecast_seed=None,
    battery_initial_soc_percent=65,
    load_profile_kw=None,
    pv_peak_kw=P_PV_PEAK,
    battery_capacity_kwh=BATTERY_CAPACITY_KWH,
    battery_charge_max_kw=BATTERY_POWER_KW,
    battery_discharge_max_kw=BATTERY_POWER_KW,
):
    """Units of a new actor. demand_size is a constant load in kW, None the
    hourly load_profile_kw (default: the household profile of
    hackathon_backend/profiles.py); pv_profile is the irradiance of the day
    in W/m² per step (default: clear sky). Every actor of a run gets the
    same units."""
    new_actor_id = uuid4()
    root_vpp = VPP()
    # Load
    if demand_size is None:
        p_profile_day = hourly_to_steps(HOUSEHOLD_LOAD_KW if load_profile_kw is None else load_profile_kw)
    else:
        p_profile_day = [demand_size for _ in range(96)]
    q_profile_day = [p / 2 for p in p_profile_day]
    root_vpp.add_unit(create_demand("d0", p_profile_day, q_profile_day, 1))
    # PV
    if pv_profile is None:
        pv_profile = clear_sky_profile()
    root_vpp.add_unit(
        create_pv_unit(
            "pb0",
            list(pv_profile),
            a_m2=4 * pv_peak_kw,
            eta_percent=25,
            forecast_seed=forecast_seed,
        )
    )
    # Battery
    root_vpp.add_unit(
        create_battery(
            "b0",
            cap_kwh=battery_capacity_kwh,
            p_charge_max_kw=battery_charge_max_kw,
            p_discharge_max_kw=battery_discharge_max_kw,
            initial_soc=battery_initial_soc_percent,
        )
    )
    return str(new_actor_id), root_vpp
