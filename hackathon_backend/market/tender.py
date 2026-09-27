"""Tender and minimum order amount profiles.

Pure functions (no state).

Plain mode
----------
The tender is the number of registered actors times the general demand per
actor (0.3 .. 0.5 kW). The minimum order amount is 1 kW, but never more than
half the tender (see plain_minimum_order_amount_kw), so at least two
minimum-sized orders fit into every auction also for small fields (with two
or three actors the tender is only 0.6 .. 1.5 kW).

Cooperative mode
----------------
Tuning rationale: an actor's default units are a 1 kW constant load, a PV
plant with 3 kW peak on a cosine irradiance (0 at step 0, peak at step 48)
and a 2 kW battery. A single actor can therefore supply roughly

    2.5 - 1.5 * cos(2 * pi * step / 96) kW   (1 kW at midnight, 4 kW at noon).

The cooperative minimum order amount is

    2.5 - 0.5 * cos(2 * pi * step / 96) kW   (2 kW at midnight, 3 kW at noon),

so by these formulas a single actor reaches the minimum alone exactly while
cos <= 0, i.e. from 06:00 to 18:00 (about half of the 96 steps); the other
half of the day needs cooperation. Note that the capacity formula
overestimates the default units by about 6 % near that boundary (the PV
model delivers a little less than its nominal peak), so measured with the
unit models the minimum is reachable alone from about 06:45 to 17:15
(steps 27..69, 43 of the 96 steps); a solo order of exactly the minimum
at 06:00 or 18:00 under-delivers slightly (see tests/market/test_tender.py).
The minimum does NOT scale with the number of participants: it is defined
relative to what one actor can supply.

The tender does scale with the number of participants, exactly like in the
plain mode: number of registered actors times the general demand per actor
(0.3 .. 0.5 kW). It is floored at minimum / share (share =
config.cooperative_min_order_share), so the minimum is never more than that
share of the tender.

Which share fits which field size (measured with the unit models; an actor
can sell at most about 14.8 kWh per day without penalty, the plain tender
asks for 9.2 kWh per actor and day):

* share 1.0 (default): the tender is at least the minimum (2.0 .. 3.0 kW),
  so one minimum-sized bid always fits and bids compete for it. With 3
  actors the field can deliver about 78 % of the tender energy, with 4
  actors 104 %, with 5 actors 130 %; from 7 actors on the plain tender
  exceeds the floor at night and the mode scales like the plain mode.
* share 0.5: the tender is at least twice the minimum (4.0 .. 6.0 kW), two
  minimum-sized bids fit. That floor is active all day for up to 10 actors,
  so with 2 to 4 actors the field cannot fill the tender at night at all
  (no competition, the price cap is the dominant strategy); it keeps the
  night tight for 8 to 10 actors (103 .. 129 % of the tender energy).
"""

import math

SIMULATION_TIME_SECONDS_PER_STEP = 900
STEPS_PER_DAY = 96

PLAIN_MINIMUM_ORDER_AMOUNT_KW = 1.0
# the plain minimum is at most this share of the tender
PLAIN_MIN_ORDER_SHARE = 0.5
SMALLEST_MINIMUM_ORDER_AMOUNT_KW = 0.1


def supply_step_from_time(supply_start_time) -> int:
    """Return the step of the day (0..95) of a supply start time in seconds."""
    return int(supply_start_time // SIMULATION_TIME_SECONDS_PER_STEP) % STEPS_PER_DAY


def plain_minimum_order_amount_kw(tender_kw: float) -> float:
    """Minimum order amount of the plain mode: 1 kW, but never more than half
    the tender (floored to 0.1 kW, at least 0.1 kW), so at least two
    minimum-sized orders fit into every auction."""
    half = math.floor(tender_kw * PLAIN_MIN_ORDER_SHARE * 10 + 1e-9) / 10
    return max(
        SMALLEST_MINIMUM_ORDER_AMOUNT_KW, min(PLAIN_MINIMUM_ORDER_AMOUNT_KW, half)
    )


def cooperative_minimum_order_amount_kw(supply_step: int) -> float:
    """Minimum order amount in kW for the supply step (2.0 .. 3.0 kW),
    independent of the number of participants."""
    return round(2.5 - 0.5 * math.cos(2 * math.pi * supply_step / STEPS_PER_DAY), 1)


def cooperative_tender_floor_kw(supply_step: int, min_order_share: float) -> float:
    """Smallest tender (rounded up to 0.1 kW) for which the minimum order
    amount is at most min_order_share of the tender."""
    minimum = cooperative_minimum_order_amount_kw(supply_step)
    return math.ceil(minimum / min_order_share * 10 - 1e-9) / 10


def cooperative_tender_amount_kw(
    supply_step: int, demand_tender_kw: float, min_order_share: float
) -> float:
    """Tender amount in kW for the supply step.

    :param demand_tender_kw: tender of the plain mode, i.e. number of
        registered actors times the general demand per actor
    :param min_order_share: the minimum order amount is at most this share
        of the tender (config.cooperative_min_order_share)
    :return: the plain-mode tender, floored at minimum / share
    """
    return round(
        max(
            demand_tender_kw,
            cooperative_tender_floor_kw(supply_step, min_order_share),
        ),
        1,
    )
