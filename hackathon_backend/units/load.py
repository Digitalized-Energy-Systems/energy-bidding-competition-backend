from typing import List, Dict
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

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        p, q = self._simple_demand.forecast_demand(step)
        return UnitResult(p_kw=p, q_kvar=q)

    def get_forecast(self, start_index, end_index):
        # TODO specify time frame of forecast
        forecast = []
        for i in range(start_index, end_index):
            forecast.append(self._simple_demand.forecast_demand(i))
        return forecast

def create_demand(id, p_profile: List, q_profile: List, uncertainty: float):
    # TODO Default Profile
    return SimpleDemandUnit(id, SimpleDemand(p_profile, q_profile, uncertainty))

