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
    # TODO specify pv profile
    new_actor_id = uuid4()
    root_vpp = VPP()
    p_profile_day = [demand_size for _ in range(96)]
    q_profile_day = [demand_size+1 for _ in range(96)]
    id = "d0"
    root_vpp.add_unit(id, create_demand(id, p_profile_day, q_profile_day, 1))
    id = "pb0"
    root_vpp.add_unit(id, create_pv_unit(id))
    id = "b0"
    root_vpp.add_unit(id, create_battery(id))
    return new_actor_id, root_vpp
