"""Robustness of the stepping loop (contract section 3) and the end of the
day (section 9)."""

import logging
import pytest
import hackathon_backend.interface as interface
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.persistence import ControllerData
from tests.helpers import FAST_STEPPING, PLAIN_CONFIG, wait_until, write_config

# with four actors the plain tender (4 * 0.3 kW at night) fits a 1 kW order
PARTICIPANTS = ["TestA", "TestB", "TestC", "TestD"]


def _all_auctions(controller):
    return list(controller.market.auctions.values()) + controller.market.expired_auctions


@pytest.mark.anyio
async def test_unreadable_config_keeps_the_last_good_config(workdir, caplog):
    # GIVEN
    config_file = workdir / "config.json"
    controller = Controller(
        write_config(config_file, max_steps=12),
        registration_keys_file=str(workdir / "keys.json"),
    )
    good = controller.config

    # WHEN the file is empty, no json, misses a field, has an invalid value
    # or is gone
    for broken in ("", "{ not json", '{"participants": []}', None):
        if broken is None:
            write_config(config_file, battery_initial_soc_percent=120)
        else:
            config_file.write_text(broken)
        controller._reload_config()
        # THEN
        assert controller.config is good
    config_file.unlink()
    controller._reload_config()
    assert controller.config is good
    assert "Keeping the last good config" in caplog.text

    # WHEN it is readable again, it is used
    write_config(config_file, max_steps=13)
    controller._reload_config()
    assert controller.config.max_steps == 13


@pytest.mark.anyio
async def test_stepping_continues_with_an_unreadable_config(api, workdir):
    # GIVEN a running loop
    app, controller = api(**FAST_STEPPING)
    controller.init()
    await wait_until(lambda: controller.step >= 2)
    good = controller.config

    # WHEN the config file becomes unreadable
    (workdir / "config.json").write_text("{ half written")
    step = controller.step

    # THEN it keeps stepping with the last good config
    await wait_until(lambda: controller.step >= step + 3)
    assert controller.config == good
    assert not controller._main_loop.done()

    # AND picks up the repaired file
    write_config(workdir / "config.json", **FAST_STEPPING, max_steps=90)
    await wait_until(lambda: controller.config.max_steps == 90)


@pytest.mark.anyio
async def test_failing_after_step_hook_is_logged_and_the_others_run(caplog):
    controller = Controller(str(PLAIN_CONFIG))
    calls = []

    def failing(controller):
        raise RuntimeError("disk full")

    controller.add_after_step_hook(failing)
    controller.add_after_step_hook(lambda c: calls.append("next"))
    with caplog.at_level(logging.ERROR):
        controller._run_after_step_hooks()

    assert calls == ["next"]
    assert "An after-step hook failed" in caplog.text
    assert "disk full" in caplog.text


@pytest.mark.anyio
async def test_failing_after_step_hook_does_not_stop_stepping(api, workdir):
    # GIVEN a hook which always fails in front of the persistence hook
    app, controller = api(**FAST_STEPPING)
    steps_seen = []

    def failing(controller):
        raise RuntimeError("disk full")

    controller.after_step_hooks.insert(0, failing)
    controller.add_after_step_hook(lambda c: steps_seen.append(c.step))

    # WHEN
    controller.init()
    await wait_until(lambda: len(steps_seen) >= 3)

    # THEN the loop keeps stepping and the other hooks still run
    assert steps_seen[:3] == [1, 2, 3]
    assert not controller._main_loop.done()
    state = ControllerData.model_validate_json((workdir / "app_state.json").read_text())
    assert state.step >= 3


@pytest.mark.anyio
@pytest.mark.parametrize("max_steps", [96, 8, 5])
async def test_no_auction_supplies_at_or_after_the_last_step(workdir, max_steps):
    # GIVEN
    controller = Controller(
        write_config(workdir / "config.json", max_steps=max_steps),
        registration_keys_file=str(workdir / "keys.json"),
    )
    controller.general_demand = create_general_demand("gd0")

    # WHEN the market steps through the whole day
    auctions = {}
    for step in range(max_steps):
        controller.step_market(current_time=step * 900)
        auctions.update({auction.id: auction for auction in _all_auctions(controller)})

    # THEN every auction supplies before max_steps * 900, the first one at
    # step 5
    supply_starts = sorted(a.params.supply_start_time for a in auctions.values())
    assert supply_starts == [900 * step for step in range(5, max_steps)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "n_actors, step, minimum_kw",
    [(1, 0, 0.1), (2, 0, 0.1), (3, 0, 0.1), (4, 71, 0.1)],
)
async def test_the_plain_mode_has_no_minimum_order(workdir, n_actors, step, minimum_kw):
    # GIVEN a plain market with a small field
    controller = Controller(
        write_config(workdir / "config.json", participants=PARTICIPANTS),
        registration_keys_file=str(workdir / "keys.json"),
    )
    controller.general_demand = create_general_demand("gd0")
    for participant in PARTICIPANTS[:n_actors]:
        await controller.register_actor(participant)

    # WHEN the auction of the step opens
    controller.step_market(current_time=step * 900)

    # THEN its minimum is only the 0.1 kW resolution of the tender
    (auction,) = [
        a for a in controller.market.auctions.values()
        if a.params.gate_opening_time == step * 900
    ]
    assert auction.params.minimum_order_amount_kw == minimum_kw


@pytest.mark.anyio
async def test_state_written_after_the_last_step_includes_its_settlement(api):
    # GIVEN a day of 7 steps; the last auction supplies in the last step
    app, controller = api(participants=PARTICIPANTS, max_steps=7, **FAST_STEPPING)
    actor_ids = [(await controller.register_actor(p))[0] for p in PARTICIPANTS]
    a = actor_ids[0]
    unit_step_done_at_hook = []
    controller.add_after_step_hook(
        lambda c: unit_step_done_at_hook.append(c.current_unit_task.done())
    )
    last_supply_time = 6 * 900

    # WHEN A sells 1 kW into the last auction
    controller.init()
    await wait_until(
        lambda: any(
            auction.params.supply_start_time == last_supply_time
            for auction in controller.market.open_auctions
        )
    )
    assert await controller.receive_order(a, "TestA", 1.0, 10, last_supply_time)
    await wait_until(lambda: controller.step == 7)

    # THEN the hooks ran after the unit step of every step
    assert unit_step_done_at_hook == [True] * 7
    # AND the persisted state holds the settlement of the last step
    loaded = interface.persistence_handler.load()
    assert loaded.step == 7
    assert controller.actor_accounts[a].get_balance() == pytest.approx(10)
    assert loaded.actor_accounts[a].get_balance() == pytest.approx(10)
    for actor_id in actor_ids:
        soc = [
            c.unit_pool.actor_to_root[actor_id].sub_units["b0"].read_information().soc_percent
            for c in (controller, loaded)
        ]
        assert soc[0] == pytest.approx(soc[1])
    # AND no auction supplies after the day
    assert max(a.params.supply_start_time for a in _all_auctions(controller)) == last_supply_time
