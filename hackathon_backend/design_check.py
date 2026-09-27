"""Check the design of the day for fields of 6 to 15 teams.

    python -m hackathon_backend.design_check [--config config.json] [--days 300] [--out design_check.html] [--open]

Builds the units of the backend for every weather day and field size and
measures, with the perfect-foresight plan of the plotter (plot.SystemView),
whether the day fulfils the goals of its design (profiles.py,
market/tender.py): the demand is comfortably reachable but not trivially,
the own load is always coverable, the morning and the evening are sparse and
the midday oversupplied, the batteries have to step in, the tender is the
demand and holds a minimum-sized order for every field size, and the night
needs cooperative bids. The day (profiles, units, minimum order) comes from
the config (default: config.json, if there is one). One page: the goals with
their measured values, then one figure per goal. Needs plotly: pip install .[plot]
"""

import argparse
import html
import statistics
import webbrowser
from pathlib import Path

from hackathon_backend.config import Config, load_config
from hackathon_backend.general_demand import demand_per_actor_profile
from hackathon_backend.market.tender import cooperative_minimum_order_amount_kw
from hackathon_backend.plot import (
    FONT,
    GREY,
    GREY_LIGHT,
    GRID,
    INK,
    INK_SECONDARY,
    SURFACE,
    SystemView,
    _runs,
    hour,
)
from hackathon_backend.units.pool import allocate_default_actor_units
from hackathon_backend.units.weather import clear_sky_profile, pv_irradiance_profile

STEPS = 96
DT_H = 0.25
FIELD_SIZES = range(6, 16)
# per-team figures are a field of this size divided by it
REFERENCE_FIELD = 6
# the margin tests/test_supply_margin.py requires
REQUIRED_MARGIN = 1.6
BLOCK_STEPS = 8
# goals per 2 h block (hours) on the median day, as in tests/test_supply_margin.py
SPARSE_BLOCKS = {(6, 8): 4.0, (18, 20): 3.0, (20, 22): 3.0}
OVERSUPPLIED_BLOCKS = {(10, 12): 5.0, (12, 14): 5.0, (14, 16): 5.0}
SPARSE_BELOW = 4.0
OVERSUPPLIED_ABOVE = 5.0

PV_COLOR = "#eda100"
LOAD_COLOR = "#4a3aa7"
BATTERY_COLOR = "#2a78d6"
SPARSE_COLOR = "#eb6834"
OVERSUPPLIED_COLOR = "#2a78d6"
FIELD_RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
              "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
GOOD = "#0ca30c"
CRITICAL = "#d03b3b"
COOPERATION_TINT = "rgba(235, 104, 52, 0.08)"


def default_config():
    """The defaults of every setting of the day (config.py)."""
    return Config(rt_step_duration_s=1, rt_step_init_delay_s=0, pause=True, max_steps=STEPS, test_mode=True)


def field(n_teams, pv_profile, config=None):
    """The units of n_teams teams on one weather day, as the backend builds
    them from the config."""
    config = config or default_config()
    _, root = allocate_default_actor_units(
        demand_size=config.actor_load_kw,
        load_profile_kw=config.household_load_kw,
        pv_profile=pv_profile,
        pv_peak_kw=config.pv_peak_kw,
        battery_capacity_kwh=config.battery_capacity_kwh,
        battery_charge_max_kw=config.battery_charge_max_kw,
        battery_discharge_max_kw=config.battery_discharge_max_kw,
        battery_initial_soc_percent=config.battery_initial_soc_percent,
    )
    payload = root.read_full_information().model_dump_json(serialize_as_any=True)
    return SystemView({
        "config": {
            "max_steps": STEPS,
            "battery_initial_soc_percent": config.battery_initial_soc_percent,
            "cooperative_bidding": True,
            "demand_per_actor_kw": config.demand_per_actor_kw,
            "cooperative_minimum_order_kw": config.cooperative_minimum_order_kw,
        },
        "unit_pool": {"actor_to_root_payload": {f"team{i}": payload for i in range(n_teams)}},
    })


def feasible(system, target_kw):
    return all(row["not_servable_kw"] < 1e-6 and row["grid_kw"] < 1e-6 for row in system.serve(target_kw))


def block_margin(system, first_step, last_step):
    """How far the demand of these steps alone could rise, the rest at 1x."""
    def scaled(m):
        return [d * (m if first_step <= step < last_step else 1.0) for step, d in enumerate(system.demand_kw)]

    low, high = 1.0, 16.0
    for _ in range(25):
        middle = (low + high) / 2
        low, high = (middle, high) if feasible(system, scaled(middle)) else (low, middle)
    return low


def own_load_stretches_kwh(system):
    """Energy the batteries deliver for the own load per contiguous deficit
    stretch: (from midnight to sunrise, from sunset to midnight)."""
    stretches, current = [], 0.0
    for net in system.surplus_kw:
        if net < 0:
            current += -net * DT_H
        elif current:
            stretches.append(current)
            current = 0.0
    stretches.append(current)
    return stretches[0], stretches[-1]


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def hour_label(value):
    minutes = round(value * 60)
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


class Design:
    def __init__(self, days, config=None):
        self.config = config or default_config()
        self.minimum_kw = self.config.cooperative_minimum_order_kw
        self.demand_per_actor = demand_per_actor_profile(self.config.demand_per_actor_kw)
        self.seeds = list(range(days))
        profiles = {seed: pv_irradiance_profile(seed) for seed in self.seeds}
        self.fields = {seed: field(REFERENCE_FIELD, profile, self.config) for seed, profile in profiles.items()}
        self.clear = field(REFERENCE_FIELD, clear_sky_profile(), self.config)
        self.margins = {
            n: {seed: (self.fields[seed] if n == REFERENCE_FIELD else field(n, profile, self.config)).margin
                for seed, profile in profiles.items()}
            for n in FIELD_SIZES
        }
        self.clear_margins = {n: field(n, clear_sky_profile(), self.config).margin for n in FIELD_SIZES}

        by_margin = sorted(self.seeds, key=self.margins[REFERENCE_FIELD].get)
        self.median_seed = by_margin[len(by_margin) // 2]
        self.worst_seed = by_margin[0]
        self.darkest_seed = min(self.seeds, key=lambda seed: sum(self.fields[seed].pv_kw))

        self.blocks = [(b * BLOCK_STEPS, (b + 1) * BLOCK_STEPS) for b in range(STEPS // BLOCK_STEPS)]
        self.block_margins = {
            seed: [block_margin(system, first, last) for first, last in self.blocks]
            for seed, system in self.fields.items()
        }
        self.own_load = {
            seed: tuple(kwh / REFERENCE_FIELD for kwh in own_load_stretches_kwh(system))
            for seed, system in self.fields.items()
        }
        self.own_load_coverable = all(feasible(system, [0.0] * STEPS) for system in self.fields.values())
        self.battery_needed_h = {
            seed: len(system.battery_needed_steps()) * DT_H for seed, system in self.fields.items()
        }
        self.tenders = {n: [round(n * d, 1) for d in self.demand_per_actor] for n in FIELD_SIZES}

    def per_team(self, values):
        return [value / REFERENCE_FIELD for value in values]

    def solo_kw(self, system):
        """The most one team can deliver alone per step: PV and full battery
        power minus its own load."""
        return self.per_team([net + system.discharge_max_kw for net in system.surplus_kw])

    @property
    def capacity_kwh(self):
        return self.clear.capacity_kwh / REFERENCE_FIELD

    def cooperation_steps(self):
        """Steps at which no team reaches the minimum alone on any day, and
        steps at which every team does on every day."""
        minimum = self.minimum_kw
        solo = [self.solo_kw(system) for system in self.fields.values()]
        never = [s for s in range(STEPS) if all(day[s] < minimum - 1e-9 for day in solo)]
        always = [s for s in range(STEPS) if all(day[s] >= minimum - 1e-9 for day in solo)]
        return never, always

    def goals(self):
        worst = min(min(margins.values()) for margins in self.margins.values())
        median = statistics.median(statistics.median(m.values()) for m in self.margins.values())
        clear = min(self.clear_margins.values())
        night, evening = zip(*self.own_load.values())
        own_worst = max(max(night), max(evening))
        median_blocks = dict(zip(self.blocks, self.block_margins[self.median_seed]))

        def block(first_h, last_h):
            return median_blocks[(first_h * 4, last_h * 4)]

        sparse = {key: block(*key) for key in SPARSE_BLOCKS}
        oversupplied = {key: block(*key) for key in OVERSUPPLIED_BLOCKS}
        smallest_tender = min(min(t[5:]) for t in self.tenders.values())
        minimum_fits = all(
            cooperative_minimum_order_amount_kw(t, self.minimum_kw) == self.minimum_kw
            for tenders in self.tenders.values() for t in tenders
        )
        never, always = self.cooperation_steps()
        needed = sorted(self.battery_needed_h.values())
        pv_kwh = sorted(sum(self.per_team(system.pv_kw)) * DT_H for system in self.fields.values())
        clear_kwh = sum(self.per_team(self.clear.pv_kw)) * DT_H

        def span(runs):
            if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == STEPS - 1:
                runs = [[runs[-1][0], runs[0][1]]] + runs[1:-1]
            return ", ".join(f"{hour_label(hour(a))}–{hour_label(hour(b + 1))}" for a, b in runs)

        return [
            ("The demand is comfortably reachable, but not every order is awarded",
             worst >= REQUIRED_MARGIN,
             f"All teams together can serve at least {worst:.2f}× the demand at every step "
             f"(median day {median:.2f}×, clear sky {clear:.2f}×) for every field of 6 to 15 teams "
             f"on {len(self.seeds)} weather days; required: {REQUIRED_MARGIN}×."),
            ("The own load is always coverable; the battery is needed for it, but never most of it",
             self.own_load_coverable and own_worst <= 0.5 * self.capacity_kwh,
             f"No grid draw on any day without selling anything. The battery delivers at most "
             f"{max(evening):.1f} kWh from sunset to midnight and {max(night):.1f} kWh from midnight "
             f"to sunrise, i.e. {own_worst / self.capacity_kwh:.0%} of its {self.capacity_kwh:.0f} kWh."),
            ("Power is sparse in the morning and in the evening",
             all(value < SPARSE_BLOCKS[key] for key, value in sparse.items()),
             "The demand of these 2 h blocks alone could rise only "
             + ", ".join(f"{value:.1f}× ({a:02d}–{b:02d} h)" for (a, b), value in sparse.items())
             + " on the median day; real strategies reach about half of that."),
            ("At midday there is far too much power, so prices must fall",
             all(value > OVERSUPPLIED_BLOCKS[key] for key, value in oversupplied.items()),
             "The demand of these 2 h blocks alone could rise "
             + ", ".join(f"{value:.1f}× ({a:02d}–{b:02d} h)" for (a, b), value in oversupplied.items())
             + " on the median day."),
            ("The batteries have to step in for the market",
             needed[0] > 0,
             f"The demand exceeds what PV leaves after the own load for {needed[0]:.1f} .. "
             f"{needed[-1]:.1f} h a day (median {statistics.median(needed):.1f} h), from the evening "
             f"through the night and in the foggy morning."),
            ("No reserve: the tender is the demand, and a minimum order fits every auction",
             minimum_fits and smallest_tender >= self.minimum_kw,
             f"The smallest tender of 6 to 15 teams is {smallest_tender:.1f} kW (6 teams × "
             f"{min(self.demand_per_actor):.2f} kW), "
             f"above the minimum order of {self.minimum_kw} kW at every step. "
             f"Every kW the market buys is demand."),
            ("The night needs cooperative bids, by day a team sells alone",
             bool(never) and bool(always),
             f"No team reaches {self.minimum_kw} kW alone on any day at "
             f"{span(_runs(never))}; every team does on every day at {span(_runs(always))}."),
            ("The PV differs from run to run, so the forecast matters",
             pv_kwh[0] < pv_kwh[-1],
             f"PV per team {pv_kwh[0]:.1f} .. {pv_kwh[-1]:.1f} kWh a day ({pv_kwh[0] / clear_kwh:.0%} .. "
             f"{pv_kwh[-1] / clear_kwh:.0%} of a clear day), with clouds and morning fog of its own "
             f"every day."),
        ]


def _layout(fig, title, height, x_title=None, y_title=None, time_axis=True):
    fig.update_layout(
        height=height,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK),
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, size=12, color=INK)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=64, r=24, t=70, b=50),
        title=dict(text=title, x=0.005, y=0.98, yanchor="top", font=dict(size=14, color=INK_SECONDARY)),
    )
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=GRID, linecolor=GRID,
                     title_text=y_title)
    fig.update_xaxes(showgrid=False, zeroline=False, showline=True, linecolor=GRID, title_text=x_title)
    if time_axis:
        fig.update_layout(hovermode="x unified")
        fig.update_xaxes(range=[0, 24], dtick=2, ticksuffix=" h", showspikes=True, spikemode="across",
                         spikethickness=1, spikecolor=GREY, spikedash="solid")
    return fig


def _day(values):
    """Step values as a step line over the whole day."""
    return [hour(step) for step in range(STEPS + 1)], list(values) + [values[-1]]


def _shade(fig, steps, color):
    for first, last in _runs(steps):
        fig.add_vrect(x0=hour(first), x1=hour(last + 1), fillcolor=color, line_width=0, layer="below")


def figure_team_day(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    pv = {seed: design.per_team(system.pv_kw) for seed, system in design.fields.items()}
    low = [percentile([day[s] for day in pv.values()], 0.05) for s in range(STEPS)]
    high = [percentile([day[s] for day in pv.values()], 0.95) for s in range(STEPS)]
    x, y = _day(low)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(width=0, shape="hv"), showlegend=False,
                             hoverinfo="skip", legendgroup="band"))
    x, y = _day(high)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(width=0, shape="hv"), fill="tonexty",
                             fillcolor="rgba(237, 161, 0, 0.18)", name="PV, 90 % of the days",
                             hoverinfo="skip", legendgroup="band"))
    for values, name, dash in (
        (design.per_team(design.clear.pv_kw), "PV, clear sky", "dot"),
        (pv[design.median_seed], f"PV, median day (seed {design.median_seed})", "solid"),
        (pv[design.darkest_seed], f"PV, darkest day (seed {design.darkest_seed})", "dash"),
    ):
        x, y = _day(values)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name,
                                 line=dict(color=PV_COLOR, width=2 if dash == "solid" else 1.5, dash=dash,
                                           shape="hv"),
                                 hovertemplate="%{y:.2f} kW"))
    x, y = _day(design.per_team(design.clear.load_kw))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="own load", line=dict(color=LOAD_COLOR, width=2,
                             shape="hv"), hovertemplate="%{y:.2f} kW"))
    x, y = _day(design.demand_per_actor)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="market demand per team",
                             line=dict(color=INK, width=2.5, shape="hv"), hovertemplate="%{y:.2f} kW"))
    return _layout(fig, "One team's day (kW): PV varies with the weather of the run, the own load and the "
                        "market demand follow a household shape", 440, y_title="kW")


def figure_plan(design):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.6, 0.4],
                        subplot_titles=["Serving the demand with perfect foresight, median day (kW per team)",
                                        "Energy stored in the battery (kWh per team)"])
    system = design.fields[design.median_seed]
    plan = system.plan
    for key, name, color in (("from_pv_kw", "served from PV", "rgba(237, 161, 0, 0.55)"),
                             ("from_battery_kw", "served from the battery", BATTERY_COLOR)):
        x, y = _day(design.per_team([row[key] for row in plan]))
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name, stackgroup="plan", fillcolor=color,
                                 line=dict(width=0, shape="hv"), hovertemplate="%{y:.2f} kW"), row=1, col=1)
    if any(row["not_servable_kw"] > 1e-6 for row in plan):
        x, y = _day(design.per_team([row["not_servable_kw"] for row in plan]))
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="not servable", stackgroup="plan",
                                 fillcolor=CRITICAL, line=dict(width=0, shape="hv")), row=1, col=1)
    x, y = _day(design.per_team(system.demand_kw))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="demand per team", line=dict(color=INK, width=2.5,
                             shape="hv"), hovertemplate="%{y:.2f} kW"), row=1, col=1)

    x_end = [hour(step + 1) for step in range(STEPS)]
    for seed, name, dash in ((design.median_seed, "stored, serving the demand, median day", "solid"),
                             (design.darkest_seed, "stored, serving the demand, darkest day", "dash")):
        stored = design.per_team([row["stored_kwh"] for row in design.fields[seed].plan])
        fig.add_trace(go.Scatter(x=[0] + x_end, y=[design.fields[seed].initial_kwh / REFERENCE_FIELD] + stored,
                                 mode="lines", name=name, line=dict(color=BATTERY_COLOR, width=2, dash=dash),
                                 hovertemplate="%{y:.1f} kWh"), row=2, col=1)
    own = design.per_team([row["stored_kwh"] for row in system.serve([0.0] * STEPS)])
    fig.add_trace(go.Scatter(x=[0] + x_end, y=[system.initial_kwh / REFERENCE_FIELD] + own, mode="lines",
                             name="stored, own load only, median day", line=dict(color=LOAD_COLOR, width=1.5,
                             dash="dot"), hovertemplate="%{y:.1f} kWh"), row=2, col=1)
    fig.add_trace(go.Scatter(x=[0, 24], y=[design.capacity_kwh] * 2, mode="lines", name="battery capacity",
                             line=dict(color=GREY, width=1.5), hoverinfo="skip"), row=2, col=1)
    _layout(fig, "", 640, y_title=None)
    fig.update_layout(margin=dict(t=110), legend=dict(y=1.08))
    fig.update_annotations(font=dict(size=14, color=INK_SECONDARY), x=0, xanchor="left")
    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="kWh", rangemode="tozero", row=2, col=1)
    return fig


def figure_blocks(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    labels = [f"{hour(first):02.0f}–{hour(last):02.0f} h" for first, last in design.blocks]
    median = design.block_margins[design.median_seed]
    values = list(zip(*design.block_margins.values()))
    low = [percentile(v, 0.05) for v in values]
    high = [percentile(v, 0.95) for v in values]
    colors = [SPARSE_COLOR if value < SPARSE_BELOW else OVERSUPPLIED_COLOR if value > OVERSUPPLIED_ABOVE else GREY
              for value in median]
    fig.add_trace(go.Bar(
        x=labels, y=median, marker=dict(color=colors, line=dict(color=SURFACE, width=2)),
        name="median day",
        error_y=dict(type="data", symmetric=False, array=[h - m for h, m in zip(high, median)],
                     arrayminus=[m - lo for m, lo in zip(median, low)], color=INK_SECONDARY, thickness=1.2,
                     width=5),
        customdata=list(zip(low, high)),
        hovertemplate="%{x}: %{y:.1f}× on the median day, 90 % of the days %{customdata[0]:.1f} .. "
                      "%{customdata[1]:.1f}×<extra></extra>",
        showlegend=False,
    ))
    for name, color in ((f"sparse: below {SPARSE_BELOW:.0f}×", SPARSE_COLOR),
                        (f"oversupplied: above {OVERSUPPLIED_ABOVE:.0f}×", OVERSUPPLIED_COLOR),
                        ("in between", GREY)):
        fig.add_trace(go.Bar(x=[None], y=[None], name=name, marker=dict(color=color)))
    fig.add_hline(y=1, line=dict(color=INK, width=1.5))
    _layout(fig, "How far the demand of one 2 h block alone could rise, the rest of the day at 1× "
                 "(median day; whiskers: 90 % of the days)", 420, y_title="× demand (line: 1×)",
            time_axis=False)
    fig.update_yaxes(rangemode="tozero")
    fig.update_layout(bargap=0.25)
    return fig


def figure_tenders(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    for color, n in zip(FIELD_RAMP, FIELD_SIZES):
        tenders = design.tenders[n]
        x, y = _day(tenders)
        fig.add_trace(go.Scatter(x=x[5:], y=y[5:], mode="lines", name=f"{n} teams",
                                 line=dict(color=color, width=2 if n in (6, 15) else 1.3, shape="hv"),
                                 hovertemplate=f"{n} teams: %{{y:.1f}} kW<extra></extra>"))
    fig.add_trace(go.Scatter(x=[0, 24], y=[design.minimum_kw] * 2, mode="lines",
                             name=f"minimum order {design.minimum_kw} kW",
                             line=dict(color=INK, width=2, dash="dash"),
                             hovertemplate="minimum order %{y:.1f} kW<extra></extra>"))
    smallest = min(design.tenders[6][5:])
    fig.add_annotation(x=12.5, y=smallest, text=f"smallest tender {smallest:.1f} kW (6 teams)", showarrow=True,
                       arrowcolor=GREY, ax=40, ay=40, font=dict(color=INK_SECONDARY))
    _layout(fig, "Tender (= demand) of every auction by field size (kW) against the minimum order of the "
                 "cooperative mode", 440, y_title="kW")
    fig.update_yaxes(rangemode="tozero")
    return fig


def figure_solo(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    never, _ = design.cooperation_steps()
    _shade(fig, never, COOPERATION_TINT)
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="no team reaches the minimum alone on any day",
                             marker=dict(symbol="square", size=12, color="rgba(235, 104, 52, 0.3)")))
    solo = [design.solo_kw(system) for system in design.fields.values()]
    low = [min(day[s] for day in solo) for s in range(STEPS)]
    high = [max(day[s] for day in solo) for s in range(STEPS)]
    x, y = _day(low)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(width=0, shape="hv"), showlegend=False,
                             hoverinfo="skip"))
    x, y = _day(high)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(width=0, shape="hv"), fill="tonexty",
                             fillcolor="rgba(42, 120, 214, 0.18)", name="all days", hoverinfo="skip"))
    x, y = _day(design.solo_kw(design.fields[design.median_seed]))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="median day", line=dict(color=BATTERY_COLOR, width=2,
                             shape="hv"), hovertemplate="%{y:.2f} kW"))
    fig.add_trace(go.Scatter(x=[0, 24], y=[design.minimum_kw] * 2, mode="lines",
                             name=f"minimum order {design.minimum_kw} kW",
                             line=dict(color=INK, width=2, dash="dash"), hoverinfo="skip"))
    _layout(fig, "The most one team can deliver alone (PV − own load + full battery power, kW) against the "
                 "minimum order", 420, y_title="kW")
    fig.update_yaxes(range=[0, 5])
    return fig


def figure_margins(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    for color, n in zip(FIELD_RAMP, FIELD_SIZES):
        fig.add_trace(go.Box(y=list(design.margins[n].values()), name=f"{n}", marker=dict(color=color),
                             line=dict(color=color, width=1.5), fillcolor="rgba(0,0,0,0)", boxpoints="outliers",
                             showlegend=False, hovertemplate=f"{n} teams: %{{y:.2f}}×<extra></extra>"))
    fig.add_trace(go.Scatter(x=[f"{n}" for n in FIELD_SIZES], y=[design.clear_margins[n] for n in FIELD_SIZES],
                             mode="markers", name="clear sky", marker=dict(symbol="diamond-open", size=9, color=INK),
                             hovertemplate="clear sky: %{y:.2f}×<extra></extra>"))
    fig.add_hline(y=REQUIRED_MARGIN, line=dict(color=INK, width=1.5, dash="dash"),
                  annotation_text=f"required {REQUIRED_MARGIN}×", annotation_position="bottom right",
                  annotation_font=dict(color=INK_SECONDARY))
    fig.add_hline(y=1, line=dict(color=CRITICAL, width=1.5),
                  annotation_text="1×: every order would be awarded", annotation_position="bottom right",
                  annotation_font=dict(color=INK_SECONDARY))
    _layout(fig, f"How many times the demand all teams together can serve at every step, over "
                 f"{len(design.seeds)} weather days, by field size", 400, x_title="teams",
            y_title="× demand", time_axis=False)
    fig.update_yaxes(range=[0, max(design.clear_margins.values()) * 1.12])
    return fig


def figure_own_load(design):
    import plotly.graph_objects as go

    fig = go.Figure()
    night, evening = zip(*design.own_load.values())
    for values, name, color in ((evening, "sunset to midnight", LOAD_COLOR),
                                (night, "midnight to sunrise", INK_SECONDARY)):
        fig.add_trace(go.Box(x=list(values), name=name, orientation="h", boxpoints="all", jitter=0.5,
                             pointpos=0, marker=dict(color=color, size=4, opacity=0.5),
                             line=dict(color=color, width=1.5), fillcolor="rgba(0,0,0,0)", showlegend=False,
                             hovertemplate="%{x:.2f} kWh<extra></extra>"))
    soc = design.config.battery_initial_soc_percent
    for value, text, dash in ((design.capacity_kwh * soc / 100, f"charge at the start ({soc:.0f} %)", "dot"),
                              (design.capacity_kwh / 2, "half the battery", "dash"),
                              (design.capacity_kwh, "capacity", "solid")):
        fig.add_vline(x=value, line=dict(color=GREY, width=1.5, dash=dash), annotation_text=text,
                      annotation_position="top", annotation_font=dict(color=INK_SECONDARY))
    _layout(fig, "Energy the battery must deliver for the own load alone, per day (kWh per team)", 300,
            x_title="kWh", time_axis=False)
    fig.update_xaxes(range=[0, design.capacity_kwh * 1.05])
    fig.update_layout(margin=dict(l=150))
    return fig


FIGURES = [
    ("day", "What a team has and what the market wants",
     "Every team gets the same units. The PV of a run is drawn anew each time (clouds and morning fog) and "
     "is the same for all teams; the own load and the demand per team peak in the morning and in the "
     "evening, when PV is low.", figure_team_day),
    ("plan", "The batteries have to step in",
     "Where the demand (black) is above what PV leaves after the own load, it can only be served from the "
     "battery (blue): from the evening through the night until the morning fog clears. Below: serving the "
     "demand draws the battery down through the night and again in the evening, far deeper than the own "
     "load alone; the energy, not the power, of the battery is what runs short.", figure_plan),
    ("blocks", "Sparse mornings and evenings, far too much at midday",
     "Could the teams deliver more if the market wanted more in only one block? Around 2–4× in the "
     "morning and the evening, where power is sparse and prices should rise; 7–10× at midday, where they "
     "must fall.", figure_blocks),
    ("margins", "Comfortably reachable, for every field size",
     "Supply and demand both scale with the number of teams, so the margin does not depend on the field "
     "size. It stays well above 1×, so bidding high does not simply pay.", figure_margins),
    ("tenders", "No reserve: the tender is the demand",
     "The tender is the number of teams times the demand per team; nothing is added on top. From 6 teams "
     "on it holds a minimum-sized order at every step.", figure_tenders),
    ("solo", "The night needs cooperative bids",
     "At night one team cannot reach the minimum order alone on any day, so it has to join a cooperative "
     "bid; by day it sells alone.", figure_solo),
    ("own-load", "The own load needs the battery, but never most of it",
     "Without selling anything, every team covers its own load on every day. The longest deficit "
     "stretches take only a small part of the battery, so most of it is free for trading.",
     figure_own_load),
]

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design check</title>
<style>
  :root {{ --surface: {surface}; --ink: {ink}; --ink-2: {ink2}; --line: {line}; --good: {good}; --bad: {bad}; }}
  body {{ margin: 0; background: var(--surface); color: var(--ink); font-family: {font}; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  h2 {{ font-size: 18px; margin: 36px 0 4px; }}
  p.lead, p.caption {{ color: var(--ink-2); margin: 0 0 12px; max-width: 900px; line-height: 1.45; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
  td {{ border-top: 1px solid var(--line); padding: 10px 8px; vertical-align: top; line-height: 1.4; }}
  td.status {{ white-space: nowrap; font-weight: 600; width: 80px; }}
  td.status.met {{ color: var(--good); }}
  td.status.missed {{ color: var(--bad); }}
  td.goal {{ font-weight: 600; width: 34%; }}
  td.value {{ color: var(--ink-2); }}
  a {{ color: inherit; }}
</style>
</head>
<body>
<main>
<h1>Design check: {n_days} weather days, 6 to 15 teams</h1>
<p class="lead">Measured with the units, weather and profiles of the backend and a plan with perfect foresight
(the most any strategy could do; it ignores minimum orders and who cooperates with whom). Real strategies keep
reserves, face forecast errors and lose volume to cooperation, so they reach about half of the headroom shown.</p>
<table>{goals}</table>
{sections}
</main>
</body>
</html>
"""


def render(design, goals, out: Path):
    goal_rows = []
    for title, met, value in goals:
        status = "✓ met" if met else "✗ missed"
        goal_rows.append(
            f'<tr><td class="status {"met" if met else "missed"}">{status}</td>'
            f'<td class="goal">{html.escape(title)}</td><td class="value">{html.escape(value)}</td></tr>'
        )
    sections = []
    for i, (anchor, heading, caption, build) in enumerate(FIGURES):
        figure = build(design)
        body = figure.to_html(full_html=False, include_plotlyjs=i == 0, config={"responsive": True,
                              "displaylogo": False})
        sections.append(f'<section id="{anchor}"><h2>{html.escape(heading)}</h2>'
                        f'<p class="caption">{html.escape(caption)}</p>{body}</section>')
    page = PAGE.format(surface=SURFACE, ink=INK, ink2=INK_SECONDARY, line=GREY_LIGHT, good=GOOD, bad=CRITICAL,
                       font=FONT, n_days=len(design.seeds), goals="\n".join(goal_rows),
                       sections="\n".join(sections))
    out.write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(prog="python -m hackathon_backend.design_check",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=Path("config.json"),
                        help="the day to check (default: config.json; its defaults if the file does not exist)")
    parser.add_argument("--days", type=int, default=300, help="number of weather days (seeds 0..days-1)")
    parser.add_argument("--out", type=Path, default=Path("design_check.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()
    try:
        import plotly  # noqa: F401
    except ImportError:
        raise SystemExit("The design check needs plotly: pip install .[plot]")

    config = load_config(args.config) if args.config.exists() else None
    print(f"checking {args.config if config else 'the defaults'}")
    design = Design(args.days, config)
    goals = design.goals()
    render(design, goals, args.out)
    for title, met, value in goals:
        print(f"{'met   ' if met else 'MISSED'} {title}: {value}")
    print(f"wrote {args.out}")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
