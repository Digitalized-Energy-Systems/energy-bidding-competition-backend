from uuid import UUID
from typing import Dict
from .unit import Unit, UnitInput


def allocate_default_actor_units():
    # TODO
    pass


class UnitPool:
    actor_to_root: Dict[UUID, Unit]

    def __init__(self) -> None:
        self.actor_to_root = {}

    def step_actor(self, uuid: UUID, inputs: Dict[str, UnitInput], step: int):
        return self.actor_to_root[uuid].step(inputs, step)

    def insert_actor_root(self, actor: UUID, unit_root: Unit):
        self.actor_to_root[actor] = unit_root
