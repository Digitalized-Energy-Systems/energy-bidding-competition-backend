"""PV weather of the day and the PV forecast (contract section 8)."""

import statistics
import pytest
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from hackathon_backend.units.pv import (
    FORECAST_ERROR_CLIP_SD,
    FORECAST_ERROR_SD_PER_STEP,
    create_pv_unit,
)
from hackathon_backend.units.unit import UnitInput
from hackathon_backend.units.weather import (
    CLOUD_FACTOR_RANGE,
    FOG_DEPTH_RANGE,
    FOG_END_RANGE_H,
    MIN_DAILY_ENERGY_SHARE,
    _with_minimum_daily_energy,
    clear_sky_profile,
    pv_irradiance_profile,
)
from tests.helpers import PLAIN_CONFIG, write_config

SEEDS = range(50)
HORIZON = 8
# issue steps whose whole forecast window lies in daylight (actual power != 0)
DAYLIGHT_ISSUE_STEPS = range(24, 65)


def _pv_unit(forecast_seed, profile=None):
    return create_pv_unit(
        "pb0",
        clear_sky_profile() if profile is None else profile,
        a_m2=12,
        eta_percent=25,
        forecast_seed=forecast_seed,
    )


def _forecast(pv_unit, issue_step):
    pv_unit.step(UnitInput(900, 0, 0), issue_step)
    return pv_unit.read_information().forecast_pv_p_kw


def _actual_kw(pv_unit, step):
    return pv_unit.get_pv_power(UnitInput(900, None, None), step).p_kw


def _pv_forecast(units):
    return next(ui.forecast_pv_p_kw for ui in units if ui.unit_id == "pb0")


def test_same_seed_same_day_different_seed_different_day():
    assert pv_irradiance_profile(7) == pv_irradiance_profile(7)
    assert len({tuple(pv_irradiance_profile(seed)) for seed in SEEDS}) == len(SEEDS)


def test_profile_is_bounded_by_clear_sky():
    clear = clear_sky_profile()
    assert clear[0] == 0 and clear[48] == pytest.approx(1000)
    for seed in SEEDS:
        profile = pv_irradiance_profile(seed)
        assert len(profile) == 96
        for irradiance, clear_irradiance in zip(profile, clear):
            assert irradiance <= clear_irradiance + 1e-9
            # clouds and, in the morning, fog
            lowest = CLOUD_FACTOR_RANGE[0] * (1 - FOG_DEPTH_RANGE[1])
            assert irradiance >= lowest * clear_irradiance - 1e-9
    # clouds make a difference on most days
    assert sum(pv_irradiance_profile(seed)[48] < 990 for seed in SEEDS) > len(SEEDS) / 2


def test_every_day_yields_at_least_the_minimum_share_of_clear_sky_energy():
    clear = sum(clear_sky_profile())
    shares = [sum(pv_irradiance_profile(seed)) / clear for seed in range(300)]
    assert min(shares) >= MIN_DAILY_ENERGY_SHARE - 1e-6
    # the days still differ
    assert max(shares) > MIN_DAILY_ENERGY_SHARE + 0.03


def test_brightening_a_dark_day_keeps_the_shape_of_its_clouds():
    # GIVEN a day far below the minimum energy
    clear = clear_sky_profile()
    factors = [0.3 + 0.4 * ((step * 7) % 11) / 10 for step in range(96)]

    # WHEN it is brightened
    brightened = _with_minimum_daily_energy(factors, clear)

    # THEN it yields exactly the minimum share, stays below clear sky and a
    # cloudier step stays at least as cloudy as a clearer one
    energy = sum(c * f for c, f in zip(clear, brightened)) / sum(clear)
    assert energy == pytest.approx(MIN_DAILY_ENERGY_SHARE, abs=1e-6)
    assert max(brightened) <= 1.0
    for i in range(96):
        for j in range(96):
            if factors[i] < factors[j]:
                assert brightened[i] <= brightened[j]


@pytest.mark.anyio
async def test_configured_pv_seed_fixes_the_day(tmp_path):
    seeded = write_config(tmp_path / "seeded.json", pv_seed=1234)
    unseeded = write_config(tmp_path / "unseeded.json")
    keys = str(tmp_path / "keys.json")

    first = Controller(seeded, registration_keys_file=keys)
    second = Controller(seeded, registration_keys_file=keys)
    assert first.pv_seed == second.pv_seed == 1234
    assert first.pv_profile == second.pv_profile == pv_irradiance_profile(1234)

    # without a seed every run draws a new day
    assert (
        Controller(unseeded, registration_keys_file=keys).pv_seed
        != Controller(unseeded, registration_keys_file=keys).pv_seed
    )


@pytest.mark.anyio
async def test_every_actor_of_a_controller_gets_the_same_weather_and_forecast():
    # GIVEN two actors of one controller (random day)
    controller = Controller(str(PLAIN_CONFIG))
    controller.general_demand = create_general_demand("gd0")
    a, _ = await controller.register_actor("TestA")
    b, _ = await controller.register_actor("TestB")

    # THEN both PV plants see the weather of the controller
    pv_a = controller.unit_pool.actor_to_root[a].sub_units["pb0"]
    pv_b = controller.unit_pool.actor_to_root[b].sub_units["pb0"]
    assert pv_a._profile == pv_b._profile == controller.pv_profile
    assert controller.pv_profile == pv_irradiance_profile(controller.pv_seed)

    # WHEN the units step into the day and both read their forecast
    controller.step_units(current_time=40 * 900)
    forecast_a = _pv_forecast(await controller.read_units(a, "TestA"))
    forecast_b = _pv_forecast(await controller.read_units(b, "TestB"))

    # THEN the forecasts are identical, also when read again
    assert len(forecast_a) == HORIZON + 1
    assert forecast_a == forecast_b
    assert _pv_forecast(await controller.read_units(a, "TestA")) == forecast_a
    assert forecast_a[0] == _actual_kw(pv_a, 40)


def test_forecast_is_deterministic_per_issue_and_target_step():
    pv_unit = _pv_unit(forecast_seed=3)
    forecast = _forecast(pv_unit, 40)

    # repeated reads and a second plant with the same weather agree
    assert pv_unit.read_information().forecast_pv_p_kw == forecast
    assert _forecast(_pv_unit(forecast_seed=3), 40) == forecast
    # another weather seed draws other errors
    assert _forecast(_pv_unit(forecast_seed=4), 40)[1:] != forecast[1:]
    # a later issue step draws a new error for the same target step
    assert pv_unit.forecast_error(40, 43) != pv_unit.forecast_error(41, 43)


def test_first_forecast_value_is_the_measured_value():
    for seed in (1, 2):
        pv_unit = _pv_unit(seed, pv_irradiance_profile(seed))
        for step in (0, 30, 48, 90):
            measured = pv_unit.step(UnitInput(900, 0, 0), step).p_kw
            assert pv_unit.read_information().forecast_pv_p_kw[0] == measured


def test_forecast_error_is_bounded_unbiased_and_grows_with_lead():
    relative_errors = {lead: [] for lead in range(1, HORIZON + 1)}
    for seed in SEEDS:
        pv_unit = _pv_unit(seed)
        for issue_step in DAYLIGHT_ISSUE_STEPS:
            forecast = _forecast(pv_unit, issue_step)
            for lead in range(1, HORIZON + 1):
                actual = _actual_kw(pv_unit, issue_step + lead)
                assert forecast[lead] <= 0
                relative_errors[lead].append(forecast[lead] / actual - 1)

    previous_sd = 0
    for lead, errors in relative_errors.items():
        scale = FORECAST_ERROR_SD_PER_STEP * lead
        assert max(abs(e) for e in errors) <= FORECAST_ERROR_CLIP_SD * scale + 1e-9
        # z ~ N(0, 1) clipped at +-2: mean 0, sd about 0.96
        assert abs(statistics.fmean(errors)) / scale < 0.1
        sd = statistics.pstdev(errors)
        assert 0.85 < sd / scale < 1.05
        assert sd > previous_sd
        previous_sd = sd
    # about 9 .. 18 % sd over the bid horizon
    assert 0.07 < statistics.pstdev(relative_errors[3]) < 0.1
    assert 0.15 < statistics.pstdev(relative_errors[6]) < 0.19


def test_forecasts_of_one_target_share_part_of_their_error():
    # errors of the same target issued at different steps are correlated
    # (rho^2 = 0.49 in expectation), so averaging them does not remove it
    pairs = []
    for seed in range(400):
        unit = _pv_unit(seed)
        pairs.append((unit.forecast_error(40, 46), unit.forecast_error(42, 46)))
    assert statistics.correlation([a for a, _ in pairs], [b for _, b in pairs]) > 0.3


def test_the_forecast_gives_no_exact_lower_bound_of_the_output():
    # a tight clip would make forecast / (1 + clip * sd) a safe bound; with
    # the wide clip the error sometimes exceeds two standard deviations
    lead = 6
    errors = [_pv_unit(seed).forecast_error(40, 40 + lead) for seed in range(2000)]
    assert max(errors) > 2 * FORECAST_ERROR_SD_PER_STEP * lead
    assert statistics.mean(errors) == pytest.approx(0, abs=0.01)


def test_every_morning_has_fog_which_clears_between_7_and_9():
    # clouds vary per day, so the effect of the fog is taken over many days:
    # the share of clear sky is lower before 07:00 than at midday, and back
    # to normal after 10:00
    clear = clear_sky_profile()
    morning, late_morning, midday = [], [], []
    for seed in range(200):
        share = [p / c if c > 0 else 1.0 for p, c in zip(pv_irradiance_profile(seed), clear)]
        morning.append(statistics.mean(share[5 * 4:int(FOG_END_RANGE_H[0] * 4)]))
        late_morning.append(statistics.mean(share[10 * 4:11 * 4]))
        midday.append(statistics.mean(share[44:52]))
    fog_effect = statistics.mean(midday) - statistics.mean(morning)
    assert FOG_DEPTH_RANGE[0] * 0.8 < fog_effect < FOG_DEPTH_RANGE[1]
    assert abs(statistics.mean(midday) - statistics.mean(late_morning)) < 0.05
