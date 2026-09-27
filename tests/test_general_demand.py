import pytest
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.units.unit import UnitInput

def test_general_demand():
    gd = create_general_demand("test")
    
    for i in range(96):
        result = gd.step(UnitInput(delta_t=900, p_kw=0, q_kvar=0), i)
        print(result.p_kw)
    
        # the profile is rounded to 1 W
        assert result.p_kw == pytest.approx(round(result.p_kw, 3))
        assert 0.3 - 1e-9 <= result.p_kw <= 0.95 + 1e-9

def test_provided_share_is_the_share_of_the_tender_served_so_far():
    gd = create_general_demand("test")
    gd.notify_supply(tender_amount_kw=2.0, provided_amount_kw=2.0)
    gd.notify_supply(tender_amount_kw=2.0, provided_amount_kw=1.0)

    assert gd.supply["provided_share_until"].iloc[0] == 1.0
    assert gd.supply["provided_share_until"].iloc[-1] == 0.75


def test_demand_peaks_in_the_morning_and_evening_and_is_low_at_midday():
    gd = create_general_demand("test")
    by_hour = {
        hour: gd.step(UnitInput(delta_t=900, p_kw=0, q_kvar=0), hour * 4).p_kw for hour in range(24)
    }
    assert by_hour[19] == max(by_hour.values())
    assert by_hour[8] > by_hour[12] and by_hour[19] > by_hour[12]
    assert by_hour[12] == min(by_hour.values())
