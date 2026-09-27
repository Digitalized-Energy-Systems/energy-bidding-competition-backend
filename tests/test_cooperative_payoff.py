"""End-to-end points distribution of a cooperative bid.

The group order of a filled cooperative bid is awarded by the market and
credited to every member's OWN account by its awarded share, i.e. by the
amount each member put into the bid. The actors get a clear-sky day, so their
delivery at noon does not depend on the weather of the run.
"""

import pytest
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.units.weather import clear_sky_profile
from tests.helpers import COOPERATIVE_CONFIG

# an auction created at 38700 s supplies at 43200 s (step 48, noon), where
# each actor can deliver ~4 kW
NOON_SUPPLY_TIME = 43200
AUCTION_CREATION_TIME = NOON_SUPPLY_TIME - 4500
PRICE_CT = 10


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cooperative_bid_payoff_is_split_by_member_amount():
    # GIVEN two bidding actors in a field of six and the auction supplying at
    # noon
    controller = Controller(config_file=str(COOPERATIVE_CONFIG))
    controller.general_demand = create_general_demand("gd0")
    controller.pv_profile = clear_sky_profile()
    a, _ = await controller.register_actor("TestA")
    b, _ = await controller.register_actor("TestB")
    others = [
        (await controller.register_actor(participant_id))[0]
        for participant_id in ("TestC", "TestD", "TestE", "TestF")
    ]
    controller.step_market(current_time=AUCTION_CREATION_TIME)
    auctions = await controller.return_open_auction_params()
    assert len(auctions) == 1
    assert auctions[0]["supply_start_time"] == NOON_SUPPLY_TIME
    target = auctions[0]["minimum_order_amount_kw"]
    assert target == pytest.approx(1.8)

    # WHEN A proposes 1 kW at the minimum and B joins with the rest
    bid = await controller.propose_cooperative_bid(
        a, "TestA", 1.0, PRICE_CT, NOON_SUPPLY_TIME, target_amount_kw=target
    )
    bid, accepted = await controller.join_cooperative_bid(b, "TestB", bid.id, 100)
    assert accepted == pytest.approx(target - 1.0)
    assert bid.status == "closed"
    assert bid.order_placed is True

    # AND the market advances until the auction is cleared
    for k in range(1, 5):
        controller.step_market(current_time=AUCTION_CREATION_TIME + 900 * k)
    results = controller.market.get_current_auction_results()
    assert f"{NOON_SUPPLY_TIME}_electricity" in results
    result = results[f"{NOON_SUPPLY_TIME}_electricity"]
    assert len(result.awarded_orders) == 1
    assert result.awarded_orders[0].agents == [a, b]
    assert result.awarded_orders[0].awarded_amount_kw == pytest.approx(
        [1.0, target - 1.0]
    )
    assert result.clearing_price == PRICE_CT
    assert bid.status == "closed"

    # AND the units deliver at noon
    controller.step_units(current_time=NOON_SUPPLY_TIME)

    # THEN every member is credited on its own account by its share
    assert set(controller.actor_accounts.keys()) == {a, b, *others}
    assert controller.actor_accounts[a].get_balance() == pytest.approx(
        PRICE_CT * 1.0, abs=1e-6
    )
    assert controller.actor_accounts[b].get_balance() == pytest.approx(
        PRICE_CT * (target - 1.0), abs=1e-6
    )
    assert set(controller.get_balance_dict_sync().keys()) == {a, b, *others}
    for actor_id in others:
        assert controller.actor_accounts[actor_id].get_balance() == pytest.approx(0, abs=1e-6)
