"""The plotter renders a persisted state into one page and two tables."""

import csv
import json
import pytest

pytest.importorskip("plotly")

from hackathon_backend.controller import Controller  # noqa: E402
from hackathon_backend.general_demand import create_general_demand  # noqa: E402
from hackathon_backend.persistence import JsonPersistenceHandler  # noqa: E402
from hackathon_backend.plot import PANELS, Situation, build_figure, render  # noqa: E402
from tests.helpers import write_config  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_a_persisted_run_is_plotted_with_every_team_and_panel(workdir):
    # GIVEN a run of two teams, one of which sold in the evening
    controller = Controller(
        write_config(workdir / "config.json", pv_seed=3), registration_keys_file=str(workdir / "keys.json")
    )
    controller.general_demand = create_general_demand("gd0")
    a, _ = await controller.register_actor("TestA")
    b, _ = await controller.register_actor("TestB")
    for step in range(80):
        controller.step_market(current_time=step * 900)
        auction = next(
            (x for x in controller.market.auctions.values() if x.params.gate_opening_time == step * 900),
            None,
        )
        if step == 70:
            await controller.receive_order(a, "TestA", 1.0, 300, auction.params.supply_start_time)
        controller.step_units(current_time=step * 900)
        controller.step = step + 1
    state_file = workdir / "app_state.json"
    JsonPersistenceHandler(str(state_file)).write(controller)

    # WHEN it is plotted
    out = workdir / "situation.html"
    situation = render(state_file, out)

    # THEN every team and panel is there, with one history row per step
    assert list(situation.teams) == [a, b]
    assert all(len(situation.history[actor]) == 80 for actor in (a, b))
    figure = build_figure(Situation(json.loads(state_file.read_text())))
    titles = [annotation.text for annotation in figure.layout.annotations]
    assert titles == [title for _, title in PANELS]
    assert {trace.legendgroup for trace in figure.data} >= {a, b}
    page = out.read_text(encoding="utf-8")
    assert "plotly" in page and "Competition situation" in page

    # AND the tables hold the series: the award of A at step 75 and its sale
    with open(workdir / "situation-teams.csv") as f:
        rows = [row for row in csv.DictReader(f) if row["actor_id"] == a]
    assert len(rows) == 80
    sale = next(row for row in rows if row["step"] == "75")
    assert float(sale["awarded_kw"]) == pytest.approx(1.0)
    assert float(sale["payoff_ct"]) == pytest.approx(300)
    with open(workdir / "situation-market.csv") as f:
        market = {row["supply_step"]: row for row in csv.DictReader(f)}
    assert float(market["75"]["clearing_price_ct"]) == 300
