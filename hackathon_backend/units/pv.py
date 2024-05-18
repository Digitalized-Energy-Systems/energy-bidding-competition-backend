from typing import List
from .unit import Unit, UnitInput, UnitResult
from pysimmods.generator.pvsim import PhotovoltaicPowerPlant

# TODO PROFILE
DEFAULT_PV_PROFILE = []
DEFAULT_CONST_TEMP = 20


class MidasPVUnit(Unit):

    def __init__(
        self, id, midas_pp: PhotovoltaicPowerPlant, pv_profile: List[float]
    ) -> None:
        super().__init__(id)

        self._midas_pp = midas_pp
        self._profile = pv_profile

    def step(self, input: UnitInput, step: int):
        self._midas_pp.set_q_kvar(input.q_kvar)
        self._midas_pp.set_p_kw(input.p_kw)
        self._midas_pp.set_step_size(input.delta_t)
        self._midas_pp.inputs.bh_w_per_m2 = self._profile[step]
        self._midas_pp.inputs.dh_w_per_m2 = self._profile[step]
        self._midas_pp.inputs.t_air_deg_celsius = DEFAULT_CONST_TEMP
        self._midas_pp.step()

        return UnitResult(
            p_kw=self._midas_pp.get_p_kw(), q_kvar=self._midas_pp.get_q_kvar()
        )


def create_pv_unit(a_m2=15, eta_percent=25, t_module_deg_celsius=25):
    return MidasPVUnit(
        PhotovoltaicPowerPlant(
            {
                "a_m2": a_m2,
                "eta_percent": eta_percent,
            },
            {"t_module_deg_celsius": t_module_deg_celsius},
        ),
        pv_profile=DEFAULT_PV_PROFILE,
    )
