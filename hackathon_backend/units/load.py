from typing import List, Dict
from .unit import Unit, UnitInput, UnitResult, UnitInformation


class DemandInformation(UnitInformation):
    perfect_demand_p_kw: List[float]
    perfect_demand_q_kvar: List[float]
    uncertainty: float


class ForecastedDemandInformation(UnitInformation):
    forecast_demand_p_kw: List[float]
    forecast_demand_q_kvar: List[float]


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

    def __init__(self, demand_information: DemandInformation) -> None:
        super().__init__(demand_information.unit_id)

        self._simple_demand = SimpleDemand(
            demand_information.perfect_demand_p_kw,
            demand_information.perfect_demand_q_kvar,
            demand_information.uncertainty,
        )
        self._internal_information = demand_information
        self.forecast_horizon = 4
        self.time_step = 0  # None

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        print(f"Step Load {self.id} with input {input} and step {step}")
        self.time_step = step
        p, q = self._simple_demand.forecast_demand(step)
        return UnitResult(p_kw=p, q_kvar=q)

    def read_information(self) -> UnitInformation:
        p, q = self.get_forecast(
            start_index=self.time_step + 1,
            end_index=self.time_step + 1 + self.forecast_horizon,
        )
        return ForecastedDemandInformation(
            unit_id=self.id, forecast_demand_p_kw=p, forecast_demand_q_kvar=q
        )

    def read_full_information(self) -> UnitInformation:
        return self._internal_information

    def get_forecast(self, start_index, end_index):
        p_forecast = []
        q_forecast = []
        for i in range(start_index, end_index):
            p_fcast, q_fcast = self._simple_demand.forecast_demand(i)
            p_forecast.append(p_fcast)
            q_forecast.append(q_fcast)
        return p_forecast, q_forecast


def create_demand(id, p_profile: List, q_profile: List, uncertainty: float):
    return SimpleDemandUnit(
        DemandInformation(
            unit_id=id,
            perfect_demand_p_kw=p_profile,
            perfect_demand_q_kvar=q_profile,
            uncertainty=uncertainty,
        )
    )
