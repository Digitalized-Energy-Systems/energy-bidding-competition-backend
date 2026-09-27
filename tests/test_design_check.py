"""The design check measures every goal of the day and renders one page."""

import pytest

from hackathon_backend.design_check import FIGURES, Design, default_config, render


@pytest.fixture(scope="module")
def design():
    return Design(12)


def test_every_goal_of_the_design_is_met(design):
    goals = design.goals()
    assert len(goals) == 8
    assert [title for title, met, _ in goals if not met] == []


def test_the_margin_does_not_depend_on_the_field_size(design):
    for seed in design.seeds:
        margins = [design.margins[n][seed] for n in design.margins]
        assert max(margins) - min(margins) < 0.1


def test_the_page_has_the_goals_and_every_figure(design, tmp_path):
    pytest.importorskip("plotly")
    out = tmp_path / "design_check.html"

    render(design, design.goals(), out)

    page = out.read_text(encoding="utf-8")
    assert page.count("✓ met") == 8
    for anchor, *_ in FIGURES:
        assert f'<section id="{anchor}">' in page


def test_the_design_is_built_from_the_config():
    config = default_config().model_copy(update={
        "demand_per_actor_kw": [0.5] * 24,
        "pv_peak_kw": 4.5,
        "battery_capacity_kwh": 20,
        "cooperative_minimum_order_kw": 1.2,
    })
    design = Design(2, config)
    assert design.tenders[6] == pytest.approx([3.0] * 96)
    assert design.minimum_kw == 1.2
    assert design.capacity_kwh == pytest.approx(20)
    default = Design(2)
    assert sum(design.clear.pv_kw) == pytest.approx(1.5 * sum(default.clear.pv_kw))
