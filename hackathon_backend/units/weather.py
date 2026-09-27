"""PV weather of the simulated day.

The irradiance is the clear-sky cosine profile times a cloud factor: a daily
clearness plus a smooth AR(1) cloud process. A new day is drawn every run
(unless config.pv_seed fixes it) and shared by all actors, so knowledge of a
previous run does not transfer and everybody has to rely on the forecast.

Every morning has fog until a random time between 07:00 and 09:00 (depth
20 .. 40 %, clearing within an hour), so the morning, when the batteries are
low after the night and the demand peaks, is the time when power is sparse;
when exactly the fog clears is only known from the forecast.

A dark day is brightened (keeping the shape of its clouds) until it yields
MIN_DAILY_ENERGY_SHARE of the clear-sky energy: the field must always be
able to deliver comfortably more than the demand, otherwise every order is
awarded and the prices do not matter (see hackathon_backend/profiles.py).
"""

import math
import random
from typing import List

STEPS_PER_DAY = 96
CLEAR_SKY_PEAK_W_PER_M2 = 1000
CLEARNESS_RANGE = (0.85, 1.0)
CLOUD_AUTOCORRELATION = 0.9
CLOUD_SD = 0.12
CLOUD_FACTOR_RANGE = (0.3, 1.0)
MIN_DAILY_ENERGY_SHARE = 0.85
FOG_END_RANGE_H = (7.0, 9.0)
FOG_DEPTH_RANGE = (0.2, 0.4)
FOG_CLEARING_STEPS = 4


def clear_sky_profile(n_steps: int = STEPS_PER_DAY) -> List[float]:
    return [
        CLEAR_SKY_PEAK_W_PER_M2 * (1 - math.cos(2 * math.pi * s / n_steps)) / 2
        for s in range(n_steps)
    ]


def cloud_factors(seed: int, n_steps: int = STEPS_PER_DAY) -> List[float]:
    rng = random.Random(f"weather/{seed}")
    clearness = rng.uniform(*CLEARNESS_RANGE)
    innovation_sd = math.sqrt(1 - CLOUD_AUTOCORRELATION**2)
    x = rng.gauss(0, 1)
    factors = []
    for _ in range(n_steps):
        x = CLOUD_AUTOCORRELATION * x + innovation_sd * rng.gauss(0, 1)
        factor = clearness + CLOUD_SD * x
        factors.append(min(CLOUD_FACTOR_RANGE[1], max(CLOUD_FACTOR_RANGE[0], factor)))
    factors = _with_morning_fog(factors, seed, n_steps)
    return _with_minimum_daily_energy(factors, clear_sky_profile(n_steps))


def _with_morning_fog(factors, seed, n_steps):
    rng = random.Random(f"fog/{seed}")
    end = rng.uniform(*FOG_END_RANGE_H) * n_steps / 24
    depth = rng.uniform(*FOG_DEPTH_RANGE)
    fogged = []
    for step, factor in enumerate(factors):
        weight = 1.0 if step + 0.5 <= end else max(0.0, 1 - (step + 0.5 - end) / FOG_CLEARING_STEPS)
        fogged.append(factor * (1 - depth * weight))
    return fogged


def _with_minimum_daily_energy(factors, clear):
    def energy_share(scale):
        return sum(c * min(1.0, f * scale) for c, f in zip(clear, factors)) / sum(clear)

    if energy_share(1.0) >= MIN_DAILY_ENERGY_SHARE:
        return factors
    # every factor is 1 at the upper end, so the bisection always brackets
    low, high = 1.0, 1.0 / min(factors)
    for _ in range(60):
        middle = (low + high) / 2
        if energy_share(middle) < MIN_DAILY_ENERGY_SHARE:
            low = middle
        else:
            high = middle
    return [min(1.0, f * high) for f in factors]


def pv_irradiance_profile(seed: int, n_steps: int = STEPS_PER_DAY) -> List[float]:
    """Irradiance in W/m² per step of the day for the weather `seed`."""
    return [
        clear * factor
        for clear, factor in zip(clear_sky_profile(n_steps), cloud_factors(seed, n_steps))
    ]
