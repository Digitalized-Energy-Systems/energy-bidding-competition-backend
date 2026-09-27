"""Round trip of auctions and of the controller state through the persistence
layer."""

import pandas as pd
import pytest
from hackathon_backend.controller import ControlException, Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.market.auction import initiate_electricity_ask_auction
from hackathon_backend.persistence import (
    AuctionData,
    ControllerData,
    JsonPersistenceHandler,
    from_auction_data,
    to_auction_data,
)
from tests.helpers import COOPERATIVE_CONFIG, PLAIN_CONFIG, write_config


def test_from_auction_data_keeps_id_status_orders_and_result():
    # GIVEN a cleared auction with one group order
    auction = initiate_electricity_ask_auction(
        0, tender_amount=4.0, minimum_order_amount_kw=2.0
    )
    auction.place_order([1.0, 1.5], 10, ["A", "B"])
    auction.step(3600)
    assert auction.status == "closed"
    assert auction.result is not None

    # WHEN it is written and restored
    restored = from_auction_data(to_auction_data(auction))

    # THEN the identity the orders and cooperative bids refer to is kept
    assert restored.id == auction.id
    assert restored.status == "closed"
    assert restored.params == auction.params
    assert [o.auction_id for o in restored.order_container.orders] == [auction.id]
    assert restored.order_container.orders == auction.order_container.orders
    # AND the result is restored instead of being dropped
    assert restored.result == auction.result
    assert restored.result.awarded_orders[0].agents == ["A", "B"]


def test_from_auction_data_keeps_id_of_open_auction():
    auction = initiate_electricity_ask_auction(900, tender_amount=4.0)
    auction.step(900)
    assert auction.status == "open"
    restored = from_auction_data(to_auction_data(auction))
    assert restored.id == auction.id
    assert restored.status == "open"
    assert restored.result is None


def test_auction_data_keeps_tender_and_minimum():
    auction = initiate_electricity_ask_auction(900, tender_amount=6.0, minimum_order_amount_kw=1.8)
    auction.step(900)

    data = AuctionData.model_validate_json(to_auction_data(auction).model_dump_json())
    restored = from_auction_data(data)

    assert restored.params.tender_amount_kw == 6.0
    assert restored.params.minimum_order_amount_kw == 1.8
    assert restored.params == auction.params


def test_auction_data_of_states_with_the_former_demand_tranche_loads():
    # states persisted while the tender had a demand tranche and a reserve
    # price carry both fields; they are ignored on load
    auction = initiate_electricity_ask_auction(900, tender_amount=4.0)
    auction.step(900)
    data = to_auction_data(auction).model_dump()
    data["params"]["demand_amount_kw"] = 1.5
    data["params"]["reserve_price_ct"] = 500

    restored = from_auction_data(AuctionData.model_validate(data))

    assert restored.params == auction.params


@pytest.mark.anyio
async def test_write_replaces_the_state_atomically(tmp_path):
    state_file = tmp_path / "app_state.json"
    state_file.write_text("old")
    controller = Controller(str(PLAIN_CONFIG), registration_keys_file=str(tmp_path / "k.json"))

    JsonPersistenceHandler(str(state_file)).write(controller)

    assert [p.name for p in tmp_path.iterdir()] == ["app_state.json"]
    assert ControllerData.model_validate_json(state_file.read_text()).step == 0


async def _controller_with_every_bid_status(workdir):
    config_file = write_config(
        workdir / "config.json",
        COOPERATIVE_CONFIG,
        participants=["TestA", {"name": "Team B", "key": "secret-b"}],
        issue_registration_keys=True,
    )
    controller = Controller(
        config_file, registration_keys_file=str(workdir / "registration_keys.json")
    )
    controller.general_demand = create_general_demand("gd0")
    carol_key = await controller.issue_registration_key("Carol", "10.0.0.1")
    a, _ = await controller.register_actor("TestA")
    b, _ = await controller.register_actor("secret-b")
    c, _ = await controller.register_actor(carol_key)
    keys = {a: "TestA", b: "secret-b", c: carol_key}

    controller.step_market(current_time=900)
    first_supply = controller.market.open_auctions[0].params.supply_start_time
    await controller.propose_cooperative_bid(a, keys[a], 0.5, 10, first_supply)
    for t in range(1800, 4501, 900):
        controller.step_market(current_time=t)
    supply = controller.market.open_auctions[-1].params.supply_start_time
    await controller.propose_cooperative_bid(a, keys[a], 0.5, 10, supply)
    closed = await controller.propose_cooperative_bid(b, keys[b], 0.5, 20, supply)
    await controller.join_cooperative_bid(c, keys[c], closed.id, 100)
    withdrawn = await controller.propose_cooperative_bid(c, keys[c], 0.5, 30, supply)
    await controller.withdraw_cooperative_bid(c, keys[c], withdrawn.id)
    controller.step_units(current_time=4500)
    controller.step = 6
    return controller, keys


@pytest.mark.anyio
async def test_controller_state_round_trip_keeps_the_new_fields(workdir):
    # GIVEN a controller with a legacy, a configured and an issued participant
    # and cooperative bids of every status
    controller, keys = await _controller_with_every_bid_status(workdir)
    a, b, c = keys
    statuses = {bid.status for bid in controller.cooperative_bids.all_bids()}
    assert statuses == {"open", "closed", "expired", "withdrawn"}

    # WHEN the state is written and loaded
    handler = JsonPersistenceHandler(str(workdir / "app_state.json"))
    handler.write(controller)
    loaded = handler.load()

    # THEN participants, names and the weather of the day are kept
    assert loaded.step == 6
    assert isinstance(loaded.registered, set)
    assert loaded.registered == controller.registered
    assert loaded.actor_to_participant == controller.actor_to_participant
    assert loaded.participant_names == controller.participant_names
    assert loaded.participant_display_names() == {a: "Tes", b: "Team B", c: "Carol"}
    assert loaded.pv_seed == controller.pv_seed
    assert loaded.pv_profile == controller.pv_profile

    # AND the cooperative bids with their statuses
    assert loaded.cooperative_bids.all_bids() == controller.cooperative_bids.all_bids()

    # AND the auction params
    assert loaded.market.auctions.keys() == controller.market.auctions.keys()
    for auction_id, auction in controller.market.auctions.items():
        assert loaded.market.auctions[auction_id].params == auction.params
    open_params = loaded.market.open_auctions[-1].params
    assert open_params.minimum_order_amount_kw == pytest.approx(
        min(1.8, open_params.tender_amount_kw)
    )

    # AND the units, which every key still authenticates
    for actor_id, key in keys.items():
        root = loaded.unit_pool.actor_to_root[actor_id]
        original = controller.unit_pool.actor_to_root[actor_id]
        assert root.read_full_information() == original.read_full_information()
        assert len(await loaded.read_units(actor_id, key)) == 3
    with pytest.raises(ControlException) as e:
        await loaded.read_units(a, keys[b])
    assert e.value.code == 403


@pytest.mark.anyio
async def test_loaded_controller_keeps_stepping_and_registering(workdir):
    # GIVEN a loaded state
    controller, keys = await _controller_with_every_bid_status(workdir)
    handler = JsonPersistenceHandler(str(workdir / "app_state.json"))
    handler.write(controller)
    loaded = handler.load()
    loaded.general_demand = create_general_demand("gd0")

    batteries = {
        actor_id: loaded.unit_pool.actor_to_root[actor_id].sub_units["b0"]
        for actor_id in keys
    }
    soc_before = {a: b.read_information().soc_percent for a, b in batteries.items()}

    # WHEN the market and the units of the loaded controller step on
    loaded.step_market(current_time=5400)
    loaded.step_units(current_time=5400)

    # THEN the batteries cover the load at night, and a new participant can
    # register
    for actor_id, battery in batteries.items():
        assert battery.read_information().soc_percent < soc_before[actor_id]
    assert set(loaded.get_balance_dict_sync().keys()) == set(keys)
    loaded.config.participants.append("TestD")
    new_actor, units = await loaded.register_actor("TestD")
    assert len(units) == 3
    assert len(loaded.registered) == 4


@pytest.mark.anyio
async def test_general_demand_history_survives_loading(workdir):
    # GIVEN a controller which supplied the general demand for a few steps
    controller = Controller(
        write_config(workdir / "config.json"), registration_keys_file=str(workdir / "keys.json")
    )
    controller.general_demand = create_general_demand("gd0")
    await controller.register_actor("TestA")
    for step in range(8):
        controller.step_market(current_time=step * 900)
        controller.step_units(current_time=step * 900)
    controller.step = 8

    # WHEN the state is written and loaded
    handler = JsonPersistenceHandler(str(workdir / "app_state.json"))
    handler.write(controller)
    loaded = handler.load()

    # THEN the history is kept and grows on
    pd.testing.assert_frame_equal(
        loaded.general_demand.supply, controller.general_demand.supply, check_dtype=False
    )
    loaded.step_market(current_time=8 * 900)
    loaded.step_units(current_time=8 * 900)
    assert len(loaded.general_demand.supply) == 9


@pytest.mark.anyio
async def test_forecast_after_loading_starts_at_the_interval_which_just_ended(workdir):
    # GIVEN a controller whose units stepped until noon
    config_file = write_config(workdir / "config.json", pv_seed=5)
    controller = Controller(config_file, registration_keys_file=str(workdir / "keys.json"))
    controller.general_demand = create_general_demand("gd0")
    a, _ = await controller.register_actor("TestA")
    for step in range(48):
        controller.step_market(current_time=step * 900)
        controller.step_units(current_time=step * 900)
    controller.step = 48

    # WHEN the state is written and loaded
    handler = JsonPersistenceHandler(str(workdir / "app_state.json"))
    handler.write(controller)
    loaded = handler.load()

    # THEN the loaded actor reads the same forecast, starting at step 47
    assert await loaded.read_units(a, "TestA") == await controller.read_units(a, "TestA")
