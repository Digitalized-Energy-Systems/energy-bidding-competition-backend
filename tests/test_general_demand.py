from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.units.unit import UnitInput

def test_general_demand():
    gd = create_general_demand("test")
    
    for i in range(96):
        result = gd.step(UnitInput(delta_t=900, p_kw=0, q_kvar=0), i)
        print(result.p_kw)
    
        assert result.p_kw * 10 % 1 == 0