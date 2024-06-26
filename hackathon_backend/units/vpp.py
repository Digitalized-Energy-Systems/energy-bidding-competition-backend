from abc import abstractmethod, ABC
from typing import List, Dict
import logging

from hackathon_backend.units.unit import UnitInformation
from .unit import Unit, UnitInput, UnitResult
from .battery import BatteryUnit


VPP_ID = "-1"

logger = logging.getLogger(__name__)


class VPPStrategy(ABC):
    @abstractmethod
    def step(
        self,
        input: UnitInput,
        other_inputs: Dict[str, UnitInput],
        units: List[Unit],
        step: int,
    ) -> UnitResult:
        pass


def find_unit(id, units: List[Unit]):
    for unit in units:
        if unit.id == id:
            return unit


class BatteryAdjustVPPStrategy(VPPStrategy):
    def step(
        self,
        input: UnitInput,
        other_inputs: Dict[str, UnitInput],
        units: List[Unit],
        step: int,
    ) -> UnitResult:
        p_kw_sum = 0
        q_kvar_sum = 0

        # everthing except batteries, as they will be adjusted
        # to deliver the remaining energy as best as possible
        for unit in [unit for unit in units if not isinstance(unit, BatteryUnit)]:
            result = unit.step(input, step)
            logger.info("Unit %s result: %s", unit.id, result)
            p_kw_sum += result.p_kw
            q_kvar_sum += result.q_kvar

        remaining_request = UnitInput(
            input.delta_t,
            p_kw=input.p_kw + p_kw_sum,
            q_kvar=input.q_kvar + q_kvar_sum,
        )
        for unit in [unit for unit in units if isinstance(unit, BatteryUnit)]:
            result = unit.step(
                UnitInput(
                    remaining_request.delta_t,
                    p_kw=-remaining_request.p_kw,
                    q_kvar=-remaining_request.q_kvar,
                ),
                step,
            )
            logger.info("Unit %s result: %s", unit.id, result)
            p_kw_sum += result.p_kw
            q_kvar_sum += result.q_kvar

            # rest for next battery
            remaining_request = UnitInput(
                remaining_request.delta_t,
                p_kw=remaining_request.p_kw + result.p_kw,
                q_kvar=remaining_request.q_kvar + result.q_kvar,
            )

        return UnitResult(p_kw=-p_kw_sum, q_kvar=-q_kvar_sum)


class VPPInformation(UnitInformation):
    unit_information_list: List[UnitInformation]


class VPP(Unit):
    strategy: VPPStrategy
    sub_units: Dict[str, Unit]

    def __init__(self, strategy=BatteryAdjustVPPStrategy()):
        super().__init__(VPP_ID)

        self.strategy = strategy
        self.sub_units = {}

    def add_unit(self, unit: Unit):
        self.sub_units[unit.id] = unit

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        logger.info("Step VPP %s with input %s and step %s", self.id, input, step)
        if self.strategy is None:
            # just step if no strategy shall be applied
            for sub_unit in self.sub_units.values():
                # leaf only
                if other_inputs is not None and sub_unit.id in other_inputs:
                    sub_unit.step(
                        other_inputs[sub_unit.id], step, other_inputs=other_inputs
                    )
                else:
                    sub_unit.step(input, step, other_inputs=other_inputs)
        else:
            return self.strategy.step(
                input=input,
                other_inputs=other_inputs,
                units=self.sub_units.values(),
                step=step,
            )

    def read_information(self) -> VPPInformation:
        return VPPInformation(
            unit_id=self.id,
            unit_information_list=[
                unit.read_information() for unit in self.sub_units.values()
            ],
        )

    def read_full_information(self) -> VPPInformation:
        return VPPInformation(
            unit_id=self.id,
            unit_information_list=[
                unit.read_full_information() for unit in self.sub_units.values()
            ],
        )
