from typing import List
from .unit import Unit, UnitInput, UnitResult


class SimpleDemand:
    _perfect_demand_p_kw: List[float]
    _perfect_demand_p_kvar: List[float]
    _uncertainty: float

    def __init__(
        self, p_demand_kw: List[float], q_demand_kvar: List[float], uncertainty: float
    ) -> None:
        self._perfect_demand_p_kw = p_demand_kw
        self._perfect_demand_p_kvar = q_demand_kvar
        self._uncertainty = uncertainty

    # TODO uncertainty
    def forecast_demand(self, step):
        return self._perfect_demand_p_kw[step], self._perfect_demand_p_kvar[step]


class SimpleDemandUnit(Unit):

    def __init__(self, id, simple_demand: SimpleDemand) -> None:
        super().__init__(id)

        self._simple_demand = simple_demand

    def step(self, input: UnitInput, step: int):
        p, q = self._simple_demand.forecast_demand(step)
        return UnitResult(p_kw=p, q_kvar=q)


def create_demand(p_profile: List, q_profile: List, uncertainty: float):
    # TODO Profile
    return SimpleDemandUnit(SimpleDemand([], [], 1))
