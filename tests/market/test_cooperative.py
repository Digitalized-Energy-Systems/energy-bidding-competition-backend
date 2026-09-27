"""Unit tests of the cooperative bid pool (pure, without market)."""

import uuid
import pytest
from hackathon_backend.market.cooperative import (
    CooperativeBid,
    CooperativeBidError,
    CooperativeBidPool,
    CooperativeMember,
)

BID_JSON_KEYS = {
    "id",
    "auction_id",
    "supply_time",
    "product_type",
    "price_ct",
    "target_amount_kw",
    "filled_amount_kw",
    "remaining_amount_kw",
    "status",
    "order_placed",
    "created_time",
    "members",
}


def _propose(pool, actor_id="A", amount_kw=1.0, target_amount_kw=3.0, **kwargs):
    params = dict(
        actor_id=actor_id,
        amount_kw=amount_kw,
        price_ct=10,
        auction_id="auction-1",
        supply_time=5400,
        target_amount_kw=target_amount_kw,
        created_time=900,
    )
    params.update(kwargs)
    return pool.propose(**params)


def test_propose_creates_open_bid_with_proposer_as_first_member():
    # GIVEN
    pool = CooperativeBidPool()

    # WHEN
    bid = _propose(pool)

    # THEN
    assert uuid.UUID(bid.id)
    assert bid.status == "open"
    assert bid.order_placed is False
    assert bid.members[0].actor_id == "A"
    assert bid.members[0].amount_kw == 1.0
    assert bid.actor_ids == ["A"]
    assert bid.filled_amount_kw == pytest.approx(1.0)
    assert bid.remaining_amount_kw == pytest.approx(2.0)
    assert bid.auction_id == "auction-1"
    assert bid.supply_time == 5400
    assert bid.product_type == "electricity"
    assert pool.get(bid.id) is bid
    assert pool.all_bids() == [bid]
    assert pool.open_bids() == [bid]
    assert pool.open_bids(supply_time=5400) == [bid]
    assert pool.open_bids(supply_time=9000) == []
    assert pool.bids_for_actor("A") == [bid]
    assert pool.bids_for_actor("B") == []


def test_propose_rejects_invalid_amounts():
    pool = CooperativeBidPool()
    with pytest.raises(CooperativeBidError) as e:
        _propose(pool, amount_kw=0)
    assert e.value.code == 400
    with pytest.raises(CooperativeBidError) as e:
        _propose(pool, amount_kw=3.0, target_amount_kw=3.0)
    assert e.value.code == 400
    with pytest.raises(CooperativeBidError) as e:
        _propose(pool, amount_kw=4.0, target_amount_kw=3.0)
    assert e.value.code == 400
    assert pool.all_bids() == []


def test_propose_rejects_nan_and_inf():
    pool = CooperativeBidPool()
    for kwargs in (
        dict(amount_kw=float("nan")),
        dict(price_ct=float("nan")),
        dict(target_amount_kw=float("nan")),
        dict(amount_kw=float("inf")),
        dict(price_ct=float("inf")),
        dict(target_amount_kw=float("inf")),
    ):
        with pytest.raises(CooperativeBidError) as e:
            _propose(pool, **kwargs)
        assert e.value.code == 400
    assert pool.all_bids() == []


def test_join_rejects_nan_and_inf():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    for amount_kw in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CooperativeBidError) as e:
            pool.join("B", bid.id, amount_kw)
        assert e.value.code == 400
    # the bid is untouched and still open
    assert bid.status == "open"
    assert bid.actor_ids == ["A"]
    assert bid.remaining_amount_kw == pytest.approx(2.0)


def test_join_below_remaining_keeps_bid_open():
    # GIVEN
    pool = CooperativeBidPool()
    bid = _propose(pool)

    # WHEN
    joined, accepted = pool.join("B", bid.id, 0.5)

    # THEN
    assert joined is bid
    assert accepted == pytest.approx(0.5)
    assert bid.status == "open"
    assert bid.actor_ids == ["A", "B"]
    assert bid.filled_amount_kw == pytest.approx(1.5)
    assert bid.remaining_amount_kw == pytest.approx(1.5)
    assert pool.bids_for_actor("B") == [bid]


def test_join_overshoot_is_capped_and_closes_bid():
    # GIVEN
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)

    # WHEN
    _, accepted = pool.join("C", bid.id, 100)

    # THEN the accepted amount is the remaining amount and the bid is
    # closed; the pool does not place the order (the controller does)
    assert accepted == pytest.approx(1.5)
    assert bid.status == "closed"
    assert bid.order_placed is False
    assert bid.filled_amount_kw == pytest.approx(3.0)
    assert bid.remaining_amount_kw == pytest.approx(0.0)
    assert bid.member_amounts_kw == pytest.approx([1.0, 0.5, 1.5])
    assert pool.open_bids() == []


def test_join_exact_fill_closes_bid():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    _, accepted = pool.join("B", bid.id, 2.0)
    assert accepted == pytest.approx(2.0)
    assert bid.status == "closed"


def test_member_joining_again_tops_up_its_amount():
    # GIVEN
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)

    # WHEN the proposer and a member join again
    _, accepted_a = pool.join("A", bid.id, 0.5)
    _, accepted_b = pool.join("B", bid.id, 0.1)

    # THEN their amounts grow, no member is listed twice
    assert accepted_a == pytest.approx(0.5)
    assert accepted_b == pytest.approx(0.1)
    assert bid.actor_ids == ["A", "B"]
    assert bid.member_amounts_kw == pytest.approx([1.5, 0.6])
    assert bid.status == "open"

    # AND a top-up below the smallest amount is refused
    with pytest.raises(CooperativeBidError) as e:
        pool.join("B", bid.id, 0.05)
    assert e.value.code == 400
    assert bid.member_amounts_kw == pytest.approx([1.5, 0.6])

    # AND a top-up which fills the target closes the bid
    _, accepted = pool.join("A", bid.id, 100)
    assert accepted == pytest.approx(0.9)
    assert bid.status == "closed"
    assert bid.member_amounts_kw == pytest.approx([2.4, 0.6])


def test_join_invalid_amount_raises():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    with pytest.raises(CooperativeBidError) as e:
        pool.join("B", bid.id, 0)
    assert e.value.code == 400
    with pytest.raises(CooperativeBidError) as e:
        pool.join("B", bid.id, -1)
    assert e.value.code == 400
    assert bid.actor_ids == ["A"]


def test_join_on_closed_bid_raises():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 100)
    assert bid.status == "closed"
    with pytest.raises(CooperativeBidError) as e:
        pool.join("C", bid.id, 1.0)
    assert e.value.code == 409


def test_join_on_expired_bid_raises():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.expire_for_closed_auctions([])
    assert bid.status == "expired"
    with pytest.raises(CooperativeBidError) as e:
        pool.join("C", bid.id, 1.0)
    assert e.value.code == 409


def test_join_unknown_bid_raises():
    pool = CooperativeBidPool()
    with pytest.raises(CooperativeBidError) as e:
        pool.join("B", "does-not-exist", 1.0)
    assert e.value.code == 404


def test_expire_for_closed_auctions_expires_only_open_bids_of_other_auctions():
    # GIVEN an open bid of auction 1, an open and a closed bid of auction 2
    pool = CooperativeBidPool()
    bid_open_1 = _propose(pool, auction_id="auction-1", supply_time=5400)
    bid_open_2 = _propose(pool, auction_id="auction-2", supply_time=6300)
    bid_closed_2 = _propose(pool, auction_id="auction-2", supply_time=6300)
    pool.join("B", bid_closed_2.id, 100)
    assert bid_closed_2.status == "closed"

    # WHEN only auction 1 is still open
    expired = pool.expire_for_closed_auctions({"auction-1"})

    # THEN
    assert expired == [bid_open_2]
    assert bid_open_1.status == "open"
    assert bid_open_2.status == "expired"
    assert bid_open_2.order_placed is False
    assert bid_closed_2.status == "closed"
    assert pool.open_bids() == [bid_open_1]
    # every status is listed for a member
    assert set(b.id for b in pool.bids_for_actor("A")) == {
        bid_open_1.id,
        bid_open_2.id,
        bid_closed_2.id,
    }

    # WHEN called again with nothing open
    expired = pool.expire_for_closed_auctions(set())

    # THEN the closed bid is untouched
    assert expired == [bid_open_1]
    assert bid_closed_2.status == "closed"


def test_open_bids_sorted_by_supply_time_then_created_time():
    pool = CooperativeBidPool()
    late = _propose(pool, auction_id="a3", supply_time=9000, created_time=4500)
    early_second = _propose(pool, auction_id="a1", supply_time=5400, created_time=1800)
    early_first = _propose(pool, auction_id="a1", supply_time=5400, created_time=900)
    assert pool.open_bids() == [early_first, early_second, late]


def test_to_dict_matches_contract_and_model_round_trip():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)

    # THEN
    as_dict = bid.to_dict()
    assert set(as_dict.keys()) == BID_JSON_KEYS
    assert as_dict["members"] == [
        {"actor_id": "A", "amount_kw": 1.0},
        {"actor_id": "B", "amount_kw": 0.5},
    ]
    assert as_dict["filled_amount_kw"] == pytest.approx(1.5)
    assert as_dict["remaining_amount_kw"] == pytest.approx(1.5)

    # the computed helpers are dumped and ignored on validation
    dumped = bid.model_dump()
    assert "filled_amount_kw" in dumped and "remaining_amount_kw" in dumped
    restored = CooperativeBid.model_validate(dumped)
    assert restored == bid
    restored_json = CooperativeBid.model_validate_json(bid.model_dump_json())
    assert restored_json == bid

    # a restored pool keeps the bids
    restored_pool = CooperativeBidPool([restored])
    assert restored_pool.get(bid.id) == bid
    assert restored_pool.get(bid.id).members[1] == CooperativeMember(
        actor_id="B", amount_kw=0.5
    )


def test_withdrawing_member_leaves_and_the_bid_stays_open():
    # GIVEN
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)
    pool.join("C", bid.id, 0.3)

    # WHEN
    withdrawn = pool.withdraw("B", bid.id)

    # THEN B's share is removed and the others stay
    assert withdrawn is bid
    assert bid.status == "open"
    assert bid.actor_ids == ["A", "C"]
    assert bid.filled_amount_kw == pytest.approx(1.3)
    assert pool.open_bids() == [bid]
    assert pool.bids_for_actor("B") == []

    # AND B may join again
    pool.join("B", bid.id, 0.2)
    assert bid.actor_ids == ["A", "C", "B"]


def test_withdrawing_proposer_cancels_the_bid():
    # GIVEN
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)

    # WHEN
    pool.withdraw("A", bid.id)

    # THEN
    assert bid.status == "withdrawn"
    assert bid.order_placed is False
    assert pool.open_bids() == []
    assert pool.bids_for_actor("B") == [bid]
    with pytest.raises(CooperativeBidError) as e:
        pool.join("C", bid.id, 1.0)
    assert e.value.code == 409
    # a withdrawn bid is no open proposal any more
    for _ in range(5):
        _propose(pool)


@pytest.mark.parametrize("status", ["closed", "expired", "withdrawn"])
def test_withdraw_from_a_bid_which_is_not_open_raises(status):
    pool = CooperativeBidPool()
    bid = _propose(pool)
    pool.join("B", bid.id, 0.5)
    if status == "closed":
        pool.join("C", bid.id, 100)
    elif status == "expired":
        pool.expire_for_closed_auctions([])
    else:
        pool.withdraw("A", bid.id)
    assert bid.status == status
    members = list(bid.members)

    for actor_id in ("A", "B"):
        with pytest.raises(CooperativeBidError) as e:
            pool.withdraw(actor_id, bid.id)
        assert e.value.code == 409
    assert bid.members == members


def test_withdraw_by_non_member_or_from_unknown_bid_raises():
    pool = CooperativeBidPool()
    bid = _propose(pool)
    with pytest.raises(CooperativeBidError) as e:
        pool.withdraw("B", bid.id)
    assert e.value.code == 400
    with pytest.raises(CooperativeBidError) as e:
        pool.withdraw("A", "does-not-exist")
    assert e.value.code == 404
    assert bid.status == "open"
    assert bid.actor_ids == ["A"]


def test_open_memberships_count_open_bids_of_the_auction():
    # GIVEN A is a member of bids in two auctions with every status
    pool = CooperativeBidPool()
    own = _propose(pool, actor_id="A", auction_id="auction-1")
    joined = _propose(pool, actor_id="B", auction_id="auction-1")
    pool.join("A", joined.id, 0.5)
    closed = _propose(pool, actor_id="C", auction_id="auction-1")
    pool.join("A", closed.id, 100)
    withdrawn = _propose(pool, actor_id="A", auction_id="auction-1")
    pool.withdraw("A", withdrawn.id)
    left = _propose(pool, actor_id="D", auction_id="auction-1")
    pool.join("A", left.id, 0.5)
    pool.withdraw("A", left.id)
    _propose(pool, actor_id="A", auction_id="auction-2")
    assert closed.status == "closed"

    # THEN only the open bids of the auction count
    assert pool.open_memberships("A", "auction-1") == 2
    assert pool.open_memberships("A", "auction-2") == 1
    assert pool.open_memberships("B", "auction-1") == 1
    assert pool.open_memberships("E", "auction-1") == 0

    # WHEN the auction closes, its bids expire and free the slots
    pool.expire_for_closed_auctions({"auction-2"})
    assert pool.open_memberships("A", "auction-1") == 0
    assert own.status == "expired"
