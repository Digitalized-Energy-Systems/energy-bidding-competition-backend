"""Tender and minimum order amount.

Pure functions (no state).

The tender of an auction is the demand of its supply interval: the number of
registered actors times the demand per actor of hackathon_backend/profiles.py
(0.35 .. 0.7 kW), rounded to 0.1 kW. There is no padding on top of it, so
every kW the market buys is really needed and prices only rise where power
is sparse.

Plain mode
----------
There is no minimum order amount beyond the 0.1 kW resolution of the tender:
every actor sells alone, any amount up to the tender.

Cooperative mode
----------------
The minimum order amount is 1.8 kW, but never more than the tender. An
actor's default units (see hackathon_backend/profiles.py and units/) can
deliver at most 1.4 .. 1.8 kW net at night and in the late evening (the
2 kW battery minus the own load, plus the last PV), so these orders need a
cooperative bid; by day an actor sells alone.

The design holds for fields of 6 to 15 actors (tests/test_supply_margin.py,
the design check plot):

- the smallest demand, 6 actors * 0.35 kW = 2.1 kW, is above the minimum, so
  a minimum-sized order fits into every auction; fewer actors still get an
  order in, because the minimum is capped at the tender
- supply and demand both scale with the field, so its margins do not depend
  on its size: all actors together can serve at least 1.75 times the demand
  at every step, but the demand of the morning and the evening alone could
  rise only 2.3 .. 3.4 times, that of the midday 7 .. 10 times
"""

SIMULATION_TIME_SECONDS_PER_STEP = 900
STEPS_PER_DAY = 96

# the resolution of the tender, i.e. no real minimum
SMALLEST_MINIMUM_ORDER_AMOUNT_KW = 0.1
PLAIN_MINIMUM_ORDER_AMOUNT_KW = SMALLEST_MINIMUM_ORDER_AMOUNT_KW
COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW = 1.8


def supply_step_from_time(supply_start_time) -> int:
    """Return the step of the day (0..95) of a supply start time in seconds."""
    return int(supply_start_time // SIMULATION_TIME_SECONDS_PER_STEP) % STEPS_PER_DAY


def _capped_at_the_tender(minimum_kw: float, tender_kw: float) -> float:
    return max(SMALLEST_MINIMUM_ORDER_AMOUNT_KW, min(minimum_kw, round(tender_kw, 1)))


def plain_minimum_order_amount_kw(
    tender_kw: float, minimum_kw: float = PLAIN_MINIMUM_ORDER_AMOUNT_KW
) -> float:
    """Minimum order amount of the plain mode: 0.1 kW (config
    plain_minimum_order_kw), never more than the tender."""
    return _capped_at_the_tender(minimum_kw, tender_kw)


def cooperative_minimum_order_amount_kw(
    tender_kw: float, minimum_kw: float = COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW
) -> float:
    """Minimum order amount of the cooperative mode: 1.8 kW (config
    cooperative_minimum_order_kw), which a single actor cannot deliver at
    night, but never more than the tender."""
    return _capped_at_the_tender(minimum_kw, tender_kw)
