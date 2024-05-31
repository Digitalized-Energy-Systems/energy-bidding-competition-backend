import sys
from typing import List, Dict
from dataclasses import dataclass
from .unit import Unit, UnitInput, UnitResult, UnitInformation
from pysimmods.generator.pvsim import PhotovoltaicPowerPlant
import datetime
import math

P_PV_PEAK = 3.0
N_TIME_INTERVALS = 96
# Profile typically between 0 and 1000 in Germany (w_per_m2):
DEFAULT_PV_PROFILE = [1000 for _ in range(N_TIME_INTERVALS)]
DEFAULT_CONST_TEMP = 20

@dataclass
class PVInformation(UnitInformation):
    forecast_pv_p_kw: List[float]


class MidasPVUnit(Unit):

    def __init__(
        self, id, midas_pp: PhotovoltaicPowerPlant, pv_profile: List[float], forecast_horizon = 8
    ) -> None:
        super().__init__(id)

        self._midas_pp = midas_pp
        self._profile = pv_profile
        self.forecast_horizon = forecast_horizon
        self.time_step = 0

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        print(f"Step PV {self.id} with input {input} and step {step}")
        self.time_step = step
        return self.get_pv_power(input, step)
    
    def get_pv_power(self, input: UnitInput, step: int):
        # step midas model
        self._midas_pp.set_p_kw(input.p_kw) # not needed for pvsim (only for pvsystemsim)
        self._midas_pp.set_q_kvar(input.q_kvar) # not needed for pvsim (only for pvsystemsim)
        self._midas_pp.set_step_size(input.delta_t)
        self._midas_pp.inputs.bh_w_per_m2 = 0 # if not 0, midas applies lat/long based irradiance model
        self._midas_pp.inputs.dh_w_per_m2 = self._profile[step]
        self._midas_pp.inputs.t_air_deg_celsius = DEFAULT_CONST_TEMP
        self._midas_pp.inputs.now_dt = datetime.datetime(
            2000, 1, 1, step // 4, (step * 15) % 60, 0, 0
        )
        print(self._midas_pp.inputs.now_dt)
        self._midas_pp.step()

        return UnitResult(
            p_kw=self._midas_pp.get_p_kw(), q_kvar=self._midas_pp.get_q_kvar()
        )

    def read_information(self) -> UnitInformation:
        return PVInformation(self.id, self.get_forecast(
            start_index=self.time_step,
            end_index=self.time_step + 1 + self.forecast_horizon,
        ))
    
    def get_forecast(self, start_index, end_index, step_size=15*60):
        # limit indices
        start_index = min(start_index, len(self._profile))
        end_index = min(end_index, len(self._profile))
        
        p_forecast = []
        for step in range(start_index, end_index):
            result = self.get_pv_power(UnitInput(step_size, None, None), step)
            p_forecast.append(result.p_kw)
            
        return p_forecast

def create_pv_unit(
    id, 
    irradiance_profile_w_per_m2=DEFAULT_PV_PROFILE,
    a_m2=4*P_PV_PEAK,
    eta_percent=25,
    t_module_deg_celsius=25
):
    return MidasPVUnit(
        id,
        PhotovoltaicPowerPlant(
            {"a_m2": a_m2, "eta_percent": eta_percent, "is_static_t_module": True},
            {"t_module_deg_celsius": t_module_deg_celsius},
        ),
        pv_profile=irradiance_profile_w_per_m2,
    )
