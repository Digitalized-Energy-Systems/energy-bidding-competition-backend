from hackathon_backend.controller import Controller
controller = Controller()

def test_get_forecast():
    # GIVEN
    agent_id = controller.create_agent()

    # WHEN
    forecast = controller.return_load_forecast(agent_id)

    # THEN
    assert forecast == [(5, 6), (5, 6), (5, 6), (5, 6)]