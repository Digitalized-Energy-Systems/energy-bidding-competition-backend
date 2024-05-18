from abc import abstractmethod, ABC
from typing import List, Dict
from .unit import Unit, UnitInput, UnitResult

VPP_ID = -1


class VPPStrategy(ABC):
    @abstractmethod
    def step(
        self, inputs: Dict[str, UnitInput], units: List[Unit], step: int
    ) -> UnitResult:
        pass


def find_unit(id, units: List[Unit]):
    for unit in units:
        if unit.id == id:
            return unit


class DefaultVPPStrategy(VPPStrategy):
    def step(
        self, inputs: Dict[str, UnitInput], units: List[Unit], step: int
    ) -> UnitResult:
        p_kw_sum = 0
        q_kvar_sum = 0
        for k, v in inputs.items():
            unit = find_unit(k, units)
            result = unit.step(v, step)
            p_kw_sum += result.p_kw
            q_kvar_sum += result.q_kvar

        return UnitResult(p_kw=p_kw_sum, q_kvar=q_kvar_sum)


class VPP(Unit):
    strategy: VPPStrategy
    sub_units: List[Unit]

    def __init__(self):
        super().__init__(VPP_ID)

    def step(self, input: Dict[str, UnitInput], step: int):
        for sub_unit in sub_unit:
            # leaf
            if sub_unit.id in input:
                sub_unit.step(input[sub_unit.id], step)
            # vpp node
            elif sub_unit.id == VPP_ID:
                sub_unit.step(input, step)
