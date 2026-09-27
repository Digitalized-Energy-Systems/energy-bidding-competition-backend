"""POST /admin/load (contract section 2)."""

import pytest
import hackathon_backend.interface as interface
from hackathon_backend.persistence import ControllerData
from tests.helpers import FAST_STEPPING, client, wait_until, write_config

PARTICIPANTS = ["TestA", "TestB", "TestC"]
LOADED_STEP = 3


def _battery_soc(controller, actor_id):
    battery = controller.unit_pool.actor_to_root[actor_id].sub_units["b0"]
    return battery.read_information().soc_percent


@pytest.mark.anyio
async def test_admin_load_is_disabled_without_a_token(api):
    # GIVEN no admin_token is configured, but a loadable state exists
    app, controller = api(**FAST_STEPPING)
    assert controller.config.admin_token is None
    interface.persistence_handler.write(controller)

    # WHEN / THEN every token is refused
    async with client(app) as ac:
        for params in ({}, {"admin_token": "anything"}, {"admin_token": ""}):
            response = await ac.post("/admin/load", params=params)
            assert response.status_code == 403
            assert "disabled" in response.json()["detail"]
    assert interface.controller is controller


@pytest.mark.anyio
async def test_admin_load_refuses_a_wrong_token(api):
    # GIVEN
    app, controller = api(admin_token="secret", **FAST_STEPPING)
    interface.persistence_handler.write(controller)

    # WHEN / THEN
    async with client(app) as ac:
        for params in ({}, {"admin_token": "wrong"}, {"admin_token": "secre"}, {"admin_token": "secret "}):
            response = await ac.post("/admin/load", params=params)
            assert response.status_code == 403
            assert response.json()["detail"] == "The admin token is wrong!"
    assert interface.controller is controller


@pytest.mark.anyio
async def test_admin_load_without_a_state_keeps_the_running_controller(api):
    app, controller = api(admin_token="secret", **FAST_STEPPING)
    controller.init()

    async with client(app) as ac:
        response = await ac.post("/admin/load", params={"admin_token": "secret"})

    assert response.status_code == 409
    assert interface.controller is controller
    step = controller.step
    await wait_until(lambda: controller.step > step)


@pytest.mark.anyio
async def test_admin_load_continues_stepping_from_the_loaded_step(api, workdir):
    # GIVEN a state persisted after LOADED_STEP steps by hand
    app, old = api(
        admin_token="secret",
        participants=PARTICIPANTS,
        rt_step_duration_s=FAST_STEPPING["rt_step_duration_s"],
        rt_step_init_delay_s=60,
    )
    a, _ = await old.register_actor("TestA")
    b, _ = await old.register_actor("TestB")
    for step in range(LOADED_STEP):
        old.step_market(current_time=step * 900)
        old.step_units(current_time=step * 900)
    old.step = LOADED_STEP
    interface.persistence_handler.write(old)
    persisted_soc = _battery_soc(old, a)
    # AND the old loop runs, waiting in its initial delay
    old.init()
    await wait_until(lambda: old.remaining_sleep > 0)
    write_config(
        workdir / "config.json", admin_token="secret", participants=PARTICIPANTS, **FAST_STEPPING
    )

    # WHEN the state is loaded
    async with client(app) as ac:
        response = await ac.post("/admin/load/", params={"admin_token": "secret"})

    # THEN
    assert response.status_code == 200, response.text
    assert response.json() == {"loaded": True, "step": LOADED_STEP}
    new = interface.controller
    assert new is not old

    # AND the old loop is stopped
    await wait_until(lambda: old._main_loop.done())
    assert old._main_loop.cancelled()
    assert old.step == LOADED_STEP

    # AND the new one steps on from the loaded step, with the hooks attached
    await wait_until(lambda: new.step >= LOADED_STEP + 2)
    gate_openings = {auction.params.gate_opening_time for auction in new.market.auctions.values()}
    assert LOADED_STEP * 900 in gate_openings
    state = ControllerData.model_validate_json((workdir / "app_state.json").read_text())
    assert state.step > LOADED_STEP
    assert list((workdir / "results").glob("agents_*.csv"))
    assert old.step == LOADED_STEP

    # AND the loaded units work: they step and can be read with the key
    assert _battery_soc(new, a) < persisted_soc
    async with client(app) as ac:
        response = await ac.get("/units/information", params={"actor_id": a, "key": "TestA"})
        assert response.status_code == 200, response.text
        assert [unit["unit_id"] for unit in response.json()["units"]] == ["d0", "pb0", "b0"]
        response = await ac.get("/units/information", params={"actor_id": b, "key": "TestA"})
        assert response.status_code == 403

        # AND the registered participants are a set a new one is added to
        assert isinstance(new.registered, set)
        response = await ac.post("/hackathon/register", params={"participant_id": "TestC"})
        assert response.status_code == 200, response.text
    assert new.registered == set(PARTICIPANTS)
