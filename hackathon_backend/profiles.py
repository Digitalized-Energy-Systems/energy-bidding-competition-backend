"""Day profiles of the own load of an actor and of the market demand.

Both are given per full hour and interpolated linearly to the 96 steps. They
are household-like: low at night, a morning peak, low at midday and a larger
evening peak. Together with the battery start (config
battery_initial_soc_percent, 65 %) and the morning fog of units/weather.py
they give the day its price structure; checked on 300 weather days (perfect
foresight, no grid draw, see tests/test_supply_margin.py):

- the own load is always coverable without selling anything; the battery
  delivers at most 3.3 kWh for it from the evening to midnight and 2.4 kWh
  from midnight to sunrise, i.e. never most of its 12 kWh
- all actors together can serve 1.75 times the demand at every step on the
  worst day (median 1.78, clear sky 2.24), so the demand is comfortably
  reachable
- how far the demand of one 2 h block alone could rise (median day): about
  3.4 in the morning (06-08 h) and 2.3-2.5 in the evening (18-24 h), where
  power is sparse, but 7-10 at midday (10-16 h), where there is far too
  much of it. Real strategies keep reserves and lose volume to cooperation,
  so they reach only about half of these values: with the sparse blocks at
  1.6-1.8 (an earlier calibration) the mornings and evenings were
  undersupplied in the agent tests and bidding the price cap paid again
- the demand per actor is never below 0.35 kW, so the demand of six actors
  (2.1 kW) always fits the cooperative minimum order of 1.8 kW; all of this
  is per actor, so it holds for any field size from six actors on
"""

from typing import List

STEPS_PER_DAY = 96

#                        0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15   16   17   18   19   20   21   22   23
HOUSEHOLD_LOAD_KW = [0.5, 0.5, 0.5, 0.5, 0.5, 0.6, 0.8, 1.0, 0.9, 0.7, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.7, 0.8, 0.9, 1.0, 1.0, 0.9, 0.7, 0.6]
DEMAND_PER_ACTOR_KW = [0.35, 0.35, 0.35, 0.35, 0.35, 0.35, 0.45, 0.6, 0.6, 0.5, 0.4, 0.35, 0.35, 0.35, 0.35, 0.4, 0.5, 0.55, 0.65, 0.7, 0.65, 0.6, 0.55, 0.45]


def hourly_to_steps(hourly: List[float], steps: int = STEPS_PER_DAY) -> List[float]:
    """Values at the full hours, linear in between (the day wraps around)."""
    steps_per_hour = steps // 24
    values = []
    for step in range(steps):
        hour, rest = divmod(step, steps_per_hour)
        fraction = rest / steps_per_hour
        values.append(hourly[hour % 24] * (1 - fraction) + hourly[(hour + 1) % 24] * fraction)
    return values
