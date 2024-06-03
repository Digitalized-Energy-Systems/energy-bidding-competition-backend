from typing import Dict
from .unit import Unit, UnitInput, UnitResult, UnitInformation
from pysimmods.buffer.batterysim.battery import Battery


class BatteryInformation(UnitInformation):
    soc_percent: float
    cap_kwh: float
    p_charge_max_kw: float
    p_discharge_max_kw: float


class BatteryUnit(Unit):
    # MARKER
    pass


class MidasBatteryUnit(BatteryUnit):

    def __init__(self, battery_info: BatteryInformation) -> None:
        super().__init__(battery_info.unit_id)

        self._midas_battery = Battery(
            {
                "cap_kwh": battery_info.cap_kwh,
                "p_charge_max_kw": battery_info.p_charge_max_kw,
                "p_discharge_max_kw": battery_info.p_discharge_max_kw,
                "soc_min_percent": 0,
                "eta_pc": [0, 0, 100],
            },
            {"soc_percent": battery_info.soc_percent},
        )

    def step(
        self, input: UnitInput, step: int, other_inputs: Dict[str, UnitInput] = None
    ):
        print(f"Step Battery {self.id} with input {input} and step {step}")
        self._midas_battery.set_q_kvar(input.q_kvar)
        self._midas_battery.set_p_kw(input.p_kw)
        self._midas_battery.set_step_size(input.delta_t)
        self._midas_battery.step()

        return UnitResult(
            p_kw=self._midas_battery.get_p_kw(), q_kvar=self._midas_battery.get_q_kvar()
        )

    def read_information(self) -> UnitInformation:
        return BatteryInformation(
            unit_id=self.id,
            soc_percent=self._midas_battery.state.soc_percent,
            cap_kwh=self._midas_battery.config.cap_kwh,
            p_charge_max_kw=self._midas_battery.config.p_charge_max_kw,
            p_discharge_max_kw=self._midas_battery.config.p_discharge_max_kw,
        )

    def read_full_information(self) -> UnitInformation:
        return self.read_information()


def create_battery(
    id,
    cap_kwh=10,
    p_charge_max_kw=2,
    p_discharge_max_kw=2,
    initial_soc=50,
):
    return MidasBatteryUnit(
        BatteryInformation(
            unit_id=id,
            cap_kwh=cap_kwh,
            p_charge_max_kw=p_charge_max_kw,
            p_discharge_max_kw=p_discharge_max_kw,
            soc_percent=initial_soc,
        ),
    )
