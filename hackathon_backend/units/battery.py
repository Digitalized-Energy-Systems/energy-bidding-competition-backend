from typing import Dict
from .unit import Unit, UnitInput, UnitResult
from pysimmods.buffer.batterysim.battery import Battery


class BatteryUnit(Unit):
    # MARKER
    pass


class MidasBatteryUnit(BatteryUnit):

    def __init__(self, id, midas_battery: Battery) -> None:
        super().__init__(id)

        self._midas_battery = midas_battery

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        self._midas_battery.set_q_kvar(input.q_kvar)
        self._midas_battery.set_p_kw(input.p_kw)
        self._midas_battery.set_step_size(input.delta_t)
        self._midas_battery.step()

        return UnitResult(
            p_kw=self._midas_battery.get_p_kw(), q_kvar=self._midas_battery.get_q_kvar()
        )


def create_battery(
    id,
    cap_kwh=5,
    p_charge_max_kw=1,
    p_discharge_max_kw=1,
    soc_min_percent=0,
    initial_soc=50,
):
    return MidasBatteryUnit(
        id,
        Battery(
            {
                "cap_kwh": cap_kwh,
                "p_charge_max_kw": p_charge_max_kw,
                "p_discharge_max_kw": p_discharge_max_kw,
                "soc_min_percent": soc_min_percent,
                "eta_pc": [0, 0, 100],
            },
            {"soc_percent": initial_soc},
        ),
    )
