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
    assert result_35.p_kw == -0.0015912545158120964
    assert result_60.p_kw == -0.0006457098380457427


def test_create_pv():
    # GIVEN
    # WHEN
    pv_unit = create_pv_unit("pv1", a_m2=11, eta_percent=23, t_module_deg_celsius=9)

    assert pv_unit.id == "pv1"
    assert pv_unit._midas_pp.config.a_m2 == 11
    assert pv_unit._midas_pp.config.eta_percent == 23
    assert pv_unit._midas_pp.state.t_module_deg_celsius == 9
