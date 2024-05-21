from uuid import UUID, uuid4
from typing import Dict
from .unit import Unit, UnitInput
from .vpp import VPP
from .load import create_demand
from .battery import create_battery
from .pv import create_pv_unit


class UnitPool:
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
        return self.actor_to_root[uuid].step(input, step, other_inputs=other_inputs)

    def insert_actor_root(self, actor: UUID, unit_root: Unit):
        self.actor_to_root[actor] = unit_root


def allocate_default_actor_units(demand_size=5):
    # TODO full generation of units
    new_actor_id = uuid4()
    root_vpp = VPP()
    profile_day = [demand_size for _ in range(96)]
    root_vpp.add_unit(create_demand("d0", profile_day, profile_day, 1))
    root_vpp.add_unit(create_pv_unit("pb0"))
    root_vpp.add_unit(create_battery("b0"))
    return new_actor_id, root_vpp
