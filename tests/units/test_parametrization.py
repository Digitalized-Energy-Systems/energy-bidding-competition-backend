import time
import math
from hackathon_backend.units.pool import allocate_default_actor_units
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
    vpp.add_unit(create_battery("b0",cap_kwh=12, p_charge_max_kw=2, p_discharge_max_kw=2, initial_soc=50))
    
    print(vpp)

    global_demand_size = local_demand_size / 3
    
    # WHEN
    energy_sum = 0.0
    for i in range(96):
        # result = vpp.sub_units["pb0"].step(
        result = vpp.step(
            input=UnitInput(
                delta_t=900,
                p_kw=0,
                q_kvar=5
            ),
            step=i
        )
        print(vpp.read_information().unit_information_list[2])
        print(result.p_kw)
        # energy_sum += result.p_kw/4
    # print(energy_sum)
    # # Add the units to the VPP
    # vpp.add_unit(unit1)
    # vpp.add_unit(unit2)
    # vpp.add_unit(unit3)

    # # Run the VPP and units over time
    # for i in range(10):
    #     vpp.run()
    #     unit1.run()
    #     unit2.run()
    #     unit3.run()
    #     time.sleep(1)  # Sleep for 1 second between each iteration

        # Assert any conditions you want to test here
    assert 0 == 1