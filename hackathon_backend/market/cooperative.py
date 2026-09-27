"""Cooperative bids: several actors pool their power into one group order.

A proposer opens a cooperative bid for an open auction with ONE common price
and a target amount. Other actors join with their own amount until the
target is filled; the pool then marks the bid "closed" and the controller
places the group order in the market (the pool itself does not know the
market). Open bids whose auction is no longer open are set to "expired".

Every amount put into a bid is at least MIN_AMOUNT_KW (or the remaining
amount, if that is smaller), and a member may top up its amount by joining
again, so a rival cannot neutralise a bid by joining it with a negligible
amount which nobody else can fill up. An actor may have at most
MAX_OPEN_BIDS_PER_ACTOR_PER_AUCTION open proposals per auction.

The awarded group order is accounted per member from each member's own
awarded share, so the value of the bid is distributed by the power amount
each member put into it; there is no joint account.
"""

import math
import uuid
import logging
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, computed_field

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE_KW = 1e-9
# smallest amount an actor may put into a bid (unless less remains)
MIN_AMOUNT_KW = 0.1
MAX_OPEN_BIDS_PER_ACTOR_PER_AUCTION = 5

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_EXPIRED = "expired"


class CooperativeBidError(Exception):
    """Raised by the pool; the controller maps it to a ControlException."""

    def __init__(self, code, message, *args: object) -> None:
        super().__init__(*args)

        self.code = code
        self.message = message


class CooperativeMember(BaseModel):
    actor_id: str
    amount_kw: float


class CooperativeBid(BaseModel):
    id: str
    auction_id: str
    supply_time: int
    product_type: str = "electricity"
    price_ct: float
    target_amount_kw: float
    members: List[CooperativeMember]
    status: str
    order_placed: bool = False
    created_time: int

    # computed fields are part of model_dump and ignored on model_validate
    @computed_field
    @property
    def filled_amount_kw(self) -> float:
        return sum(member.amount_kw for member in self.members)

    @computed_field
    @property
    def remaining_amount_kw(self) -> float:
        return max(0.0, self.target_amount_kw - self.filled_amount_kw)

    @property
    def actor_ids(self) -> List[str]:
        return [member.actor_id for member in self.members]

    @property
    def member_amounts_kw(self) -> List[float]:
        return [member.amount_kw for member in self.members]

    def is_filled(self) -> bool:
        return self.remaining_amount_kw <= AMOUNT_TOLERANCE_KW

    def has_member(self, actor_id: str) -> bool:
        return actor_id in self.actor_ids

    def member(self, actor_id: str) -> Optional[CooperativeMember]:
        for member in self.members:
            if member.actor_id == actor_id:
                return member
        return None

    @property
    def proposer_id(self) -> str:
        return self.members[0].actor_id

    def to_dict(self):
        """Bid json as returned by the REST layer."""
        return {
            "id": self.id,
            "auction_id": self.auction_id,
            "supply_time": self.supply_time,
            "product_type": self.product_type,
            "price_ct": self.price_ct,
            "target_amount_kw": self.target_amount_kw,
            "filled_amount_kw": self.filled_amount_kw,
            "remaining_amount_kw": self.remaining_amount_kw,
            "status": self.status,
            "order_placed": self.order_placed,
            "created_time": self.created_time,
            "members": [member.model_dump() for member in self.members],
        }


class CooperativeBidPool:
    """Container of cooperative bids; pure, without access to the market."""

    bids: Dict[str, CooperativeBid]

    def __init__(self, bids: Optional[List[CooperativeBid]] = None) -> None:
        self.bids = {}
        for bid in bids or []:
            self.bids[bid.id] = bid

    def get(self, cooperative_bid_id: str) -> Optional[CooperativeBid]:
        return self.bids.get(cooperative_bid_id, None)

    def propose(
        self,
        actor_id: str,
        amount_kw: float,
        price_ct: float,
        auction_id: str,
        supply_time: int,
        target_amount_kw: float,
        created_time: int,
        product_type: str = "electricity",
    ) -> CooperativeBid:
        """Create an open bid with the proposer as first member."""
        # every comparison with NaN is False, so NaN has to be refused
        # explicitly (it would neither be serializable nor ever fill)
        if not all(math.isfinite(x) for x in (amount_kw, price_ct, target_amount_kw)):
            raise CooperativeBidError(
                400,
                "The amount_kw, price_ct and target_amount_kw must be finite numbers!",
            )
        if amount_kw <= 0:
            raise CooperativeBidError(400, "The amount_kw must be greater than 0!")
        if amount_kw < MIN_AMOUNT_KW - AMOUNT_TOLERANCE_KW:
            raise CooperativeBidError(
                400, f"The amount_kw must be at least {MIN_AMOUNT_KW} kW!"
            )
        if amount_kw >= target_amount_kw - AMOUNT_TOLERANCE_KW:
            raise CooperativeBidError(
                400,
                "The amount_kw must be below the target_amount_kw, "
                "otherwise place a normal order!",
            )
        open_proposals = sum(
            1
            for bid in self.bids.values()
            if bid.status == STATUS_OPEN
            and bid.auction_id == auction_id
            and bid.proposer_id == actor_id
        )
        if open_proposals >= MAX_OPEN_BIDS_PER_ACTOR_PER_AUCTION:
            raise CooperativeBidError(
                400,
                "The actor already has "
                f"{MAX_OPEN_BIDS_PER_ACTOR_PER_AUCTION} open cooperative bids "
                "for this auction!",
            )
        bid = CooperativeBid(
            id=str(uuid.uuid4()),
            auction_id=auction_id,
            supply_time=int(supply_time),
            product_type=product_type,
            price_ct=price_ct,
            target_amount_kw=target_amount_kw,
            members=[CooperativeMember(actor_id=actor_id, amount_kw=amount_kw)],
            status=STATUS_OPEN,
            order_placed=False,
            created_time=int(created_time),
        )
        self.bids[bid.id] = bid
        logger.info("Cooperative bid proposed %s", bid)
        return bid

    def join(
        self, actor_id: str, cooperative_bid_id: str, amount_kw: float
    ) -> Tuple[CooperativeBid, float]:
        """Add a member, or top up the amount of an existing member; the
        accepted amount is capped to the remaining amount and must be at
        least MIN_AMOUNT_KW unless less remains. When the target is filled
        the bid is set to "closed" (the caller places the group order).
        Returns the bid and the accepted amount."""
        bid = self.get(cooperative_bid_id)
        if bid is None:
            raise CooperativeBidError(404, "The cooperative bid does not exist!")
        if bid.status != STATUS_OPEN:
            raise CooperativeBidError(409, "The cooperative bid is not open!")
        if not math.isfinite(amount_kw):
            raise CooperativeBidError(400, "The amount_kw must be a finite number!")
        if amount_kw <= 0:
            raise CooperativeBidError(400, "The amount_kw must be greater than 0!")
        smallest = min(MIN_AMOUNT_KW, bid.remaining_amount_kw)
        if amount_kw < smallest - AMOUNT_TOLERANCE_KW:
            raise CooperativeBidError(
                400,
                f"The amount_kw must be at least {MIN_AMOUNT_KW} kW or the "
                f"remaining amount of the bid ({bid.remaining_amount_kw} kW)!",
            )

        accepted_amount_kw = min(amount_kw, bid.remaining_amount_kw)
        member = bid.member(actor_id)
        if member is None:
            bid.members.append(
                CooperativeMember(actor_id=actor_id, amount_kw=accepted_amount_kw)
            )
        else:
            member.amount_kw += accepted_amount_kw
        if bid.is_filled():
            bid.status = STATUS_CLOSED
        logger.info(
            "Actor %s joined cooperative bid %s with %s kW",
            actor_id,
            bid.id,
            accepted_amount_kw,
        )
        return bid, accepted_amount_kw

    def expire_for_closed_auctions(self, open_auction_ids) -> List[CooperativeBid]:
        """Set every open bid whose auction is not open anymore to "expired".
        Returns the expired bids."""
        open_auction_ids = set(open_auction_ids)
        expired = []
        for bid in self.bids.values():
            if bid.status == STATUS_OPEN and bid.auction_id not in open_auction_ids:
                bid.status = STATUS_EXPIRED
                expired.append(bid)
                logger.info("Cooperative bid expired unfilled %s", bid.id)
        return expired

    def open_bids(self, supply_time: Optional[int] = None) -> List[CooperativeBid]:
        """Open bids, sorted by supply_time then created_time."""
        bids = [
            bid
            for bid in self.bids.values()
            if bid.status == STATUS_OPEN
            and (supply_time is None or bid.supply_time == supply_time)
        ]
        return sorted(bids, key=lambda bid: (bid.supply_time, bid.created_time))

    def bids_for_actor(self, actor_id: str) -> List[CooperativeBid]:
        """Bids of every status in which the actor is a member."""
        bids = [bid for bid in self.bids.values() if bid.has_member(actor_id)]
        return sorted(bids, key=lambda bid: (bid.created_time, bid.supply_time))

    def all_bids(self) -> List[CooperativeBid]:
        return list(self.bids.values())
