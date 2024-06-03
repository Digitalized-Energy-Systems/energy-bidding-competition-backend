from hackathon_backend.units.pv import *


def test_pv_step():
    # GIVEN
    pv_unit = MidasPVUnit(
        id,
        PhotovoltaicPowerPlant(
            {
                "a_m2": 1,
                "eta_percent": 2,
            },
            {"t_module_deg_celsius": 20},
        ),
        pv_profile=[10 for i in range(96)],
    )
    input = UnitInput(15 * 60, 1, 1)

    # WHEN
    result_35 = pv_unit.step(input, 35, None)
    result_60 = pv_unit.step(input, 60, None)

    # THEN
    assert result_35.p_kw == -3.163175018078267e-05
    assert result_60.p_kw == -3.163175018078273e-05


def test_create_pv():
    # GIVEN
    # WHEN
    pv_unit = create_pv_unit("pv1", a_m2=11, eta_percent=23, t_module_deg_celsius=9)

    assert pv_unit.id == "pv1"
    assert pv_unit._midas_pp.config.a_m2 == 11
    assert pv_unit._midas_pp.config.eta_percent == 23
    assert pv_unit._midas_pp.state.t_module_deg_celsius == 9


def test_check_pv_forecast():
    # GIVEN
    
    # WHEN
    # full cosine profile over N time intervals
    n_intervals = 96
    cos_values = [math.cos(2*math.pi*x/n_intervals) for x in range(n_intervals)]
    # create irradiance profile from cosine values
    pv_profile_day = [1000*(1-s)/2 for s in cos_values]
    pv_unit = create_pv_unit(
        "pv1",
        irradiance_profile_w_per_m2=pv_profile_day,
        a_m2=11,
        eta_percent=23,
        t_module_deg_celsius=9
    )
    pv_unit.step(UnitInput(15 * 60, 1, 1), 35, None)
    result = pv_unit.read_information()

    # print(result.forecast_pv_p_kw)
    # assert 0 == 1
    
    assert type(result.forecast_pv_p_kw) == list
    assert len(result.forecast_pv_p_kw) == 9