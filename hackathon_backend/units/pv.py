from typing import List, Dict
from dataclasses import dataclass
from .unit import Unit, UnitInput, UnitResult, UnitInformation
from pysimmods.generator.pvsim import PhotovoltaicPowerPlant
import datetime

# TODO PROFILE, typically between 0 and 1000 in Germany (w_per_m2)?
DEFAULT_PV_PROFILE = [1000 for _ in range(96)]
DEFAULT_CONST_TEMP = 20


@dataclass
class PVInformation(UnitInformation):
    forecast_pv_p_kw: List[float]


class MidasPVUnit(Unit):

    def __init__(
        self, id, midas_pp: PhotovoltaicPowerPlant, pv_profile: List[float]
    ) -> None:
        super().__init__(id)

        self._midas_pp = midas_pp
        self._profile = pv_profile

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        print(f'Step PV {self.id} with input {input} and step {step}')
        self._midas_pp.set_p_kw(DEFAULT_PV_PROFILE[step]) # TODO insert power value
        self._midas_pp.set_q_kvar(input.q_kvar)
        self._midas_pp.set_step_size(input.delta_t)
        self._midas_pp.inputs.bh_w_per_m2 = self._profile[step]
        self._midas_pp.inputs.dh_w_per_m2 = self._profile[step]
        self._midas_pp.inputs.t_air_deg_celsius = DEFAULT_CONST_TEMP
        self._midas_pp.inputs.now_dt = datetime.datetime(
            2000, 1, 1, step // 4, (step * 15) // 60, 0, 0
        )
        self._midas_pp.step()

        return UnitResult(
            p_kw=self._midas_pp.get_p_kw(), q_kvar=self._midas_pp.get_q_kvar()
        )

    def read_information(self) -> UnitInformation:
        return PVInformation(self.id, DEFAULT_PV_PROFILE)


def create_pv_unit(id, a_m2=15, eta_percent=25, t_module_deg_celsius=25):
    return MidasPVUnit(
        id,
        PhotovoltaicPowerPlant(
            {
                "a_m2": a_m2,
                "eta_percent": eta_percent,
            },
            {"t_module_deg_celsius": t_module_deg_celsius},
        ),
        pv_profile=DEFAULT_PV_PROFILE,
    )
