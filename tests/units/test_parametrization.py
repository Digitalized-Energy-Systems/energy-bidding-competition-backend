import math
import pytest
from hackathon_backend.units.unit import UnitInput
from hackathon_backend.units.vpp import VPP
from hackathon_backend.units.load import create_demand
from hackathon_backend.units.battery import create_battery
from hackathon_backend.units.pv import create_pv_unit


def test_vpp_over_time():
    # GIVEN
    # Create an instance of the VPP
    vpp = VPP()
    local_demand_size = 1.4
    p_profile_day = [local_demand_size for _ in range(96)]
    q_profile_day = [local_demand_size + 1 for _ in range(96)]
    vpp.add_unit(create_demand("d0", p_profile_day, q_profile_day, 1))
    # full cosine profile over N time intervals
    n_intervals = 96
    cos_values = [math.cos(2*math.pi*x/n_intervals) for x in range(n_intervals)]
    # create irradiance profile from cosine values
    pv_profile_day = [1000*(1-s)/2 for s in cos_values]
    vpp.add_unit(create_pv_unit("pb0", pv_profile_day))
    battery = create_battery("b0",cap_kwh=12, p_charge_max_kw=2, p_discharge_max_kw=2, initial_soc=50)
    vpp.add_unit(battery)
    reference_pv = create_pv_unit("pv", pv_profile_day)

    # WHEN
    energy_sum_kwh = 0.0
    soc_percent = []
    for i in range(96):
        result = vpp.step(
            input=UnitInput(
                delta_t=900,
                p_kw=0,
                q_kvar=5
            ),
            step=i
        )
        # THEN the battery balances PV and load in every step of a clear day
        assert result.p_kw == pytest.approx(0, abs=1e-9)
        soc_percent.append(battery._midas_battery.state.soc_percent)
        pv_kw = -reference_pv.step(UnitInput(900, 0, 0), i).p_kw
        energy_sum_kwh += (pv_kw - local_demand_size) / 4

    # THEN it never runs empty or full, and the energy balance of the day ends
    # up in the battery
    assert 0 < min(soc_percent) and max(soc_percent) < 100
    assert soc_percent[-1] == pytest.approx(50 + 100 * energy_sum_kwh / 12)
