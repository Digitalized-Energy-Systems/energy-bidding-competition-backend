from uuid import UUID, uuid4
from typing import Dict, List
import math
import logging

from .unit import Unit, UnitInput, UnitInformation
from .vpp import VPP, VPPInformation
from .load import create_demand
from .battery import create_battery
from .pv import create_pv_unit


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


def allocate_default_actor_units(demand_size=1):
    new_actor_id = uuid4()
    root_vpp = VPP()
    # Load
    p_profile_day = [demand_size for _ in range(96)]
    q_profile_day = [demand_size / 2 for _ in range(96)]
    root_vpp.add_unit(create_demand("d0", p_profile_day, q_profile_day, 1))
    # PV
    # full cosine profile over N time intervals
    n_intervals = 96
    cos_values = [math.cos(2 * math.pi * x / n_intervals) for x in range(n_intervals)]
    # create irradiance profile from cosine values
    pv_profile_day = [1000 * (1 - s) / 2 for s in cos_values]
    p_pv_peak = 3.0
    root_vpp.add_unit(
        create_pv_unit("pb0", pv_profile_day, a_m2=4 * p_pv_peak, eta_percent=25)
    )
    # Battery
    root_vpp.add_unit(create_battery("b0"))
    return str(new_actor_id), root_vpp
