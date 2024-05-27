from uuid import UUID, uuid4
from typing import Dict, List
from .unit import Unit, UnitInput, UnitInformation
from .vpp import VPP, VPPInformation
from .load import create_demand
from .battery import create_battery
from .pv import create_pv_unit


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

    actor_to_root: Dict[UUID, Unit]

    def __init__(self) -> None:
        self.actor_to_root = {}

    def step_actor(
        self,
        uuid: UUID,
        input: UnitInput,
        step: int,
        other_inputs: Dict[str, UnitInput] = None,
    ):
        print(f"Step actor {uuid}...")
        return self.actor_to_root[uuid].step(input, step, other_inputs=other_inputs)

    def insert_actor_root(self, actor: UUID, unit_root: Unit):
        self.actor_to_root[actor] = unit_root

    def read_units(self, actor: UUID) -> List[UnitInformation]:
        """Initiate reading of unit information and flatten it."""
        root = self.actor_to_root[actor]
        unit_information = root.read_information()
        return _flatten_unit_information(unit_information)


def allocate_default_actor_units(demand_size=4):
    # TODO full generation of units
    # TODO specify pv profile
    new_actor_id = uuid4()
    root_vpp = VPP()
    p_profile_day = [demand_size for _ in range(96)]
    q_profile_day = [demand_size + 1 for _ in range(96)]
    root_vpp.add_unit("d0", create_demand("d0", p_profile_day, q_profile_day, 1))
    root_vpp.add_unit("pb0", create_pv_unit("pb0"))
    root_vpp.add_unit("b0", create_battery("b0"))
    return new_actor_id, root_vpp
