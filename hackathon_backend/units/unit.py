from abc import ABC, abstractmethod
from typing import Optional, Dict
from dataclasses import dataclass


@dataclass
class UnitInput:
    delta_t: Optional[float]
    p_kw: Optional[float]
    q_kvar: Optional[float]


@dataclass
class UnitResult:
    p_kw: Optional[float]
    q_kvar: Optional[float]


class Unit(ABC):
    id: str

    def __init__(self, id) -> None:
        super().__init__()

        self.id = id

    @abstractmethod
    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ) -> UnitResult:
        return None
