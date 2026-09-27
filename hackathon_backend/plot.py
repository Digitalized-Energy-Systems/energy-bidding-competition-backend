"""Plot the full situation of a run from its persisted state.

    python -m hackathon_backend.plot [--state app_state.json] [--out situation.html] [--watch SECONDS]

One interactive page with stacked panels on a shared time axis. The system
view comes first: the PV, load and batteries of all teams together against
the demand and tender (where batteries are needed is shaded), a plan with
perfect foresight of how the demand can be served, and the stored energy.
Then the market (offered power, demand, minimum order, delivered power), the
share of the demand served, every order price with the clearing price, and
per team the awarded and delivered power, PV and
load, battery power and charge and the balance, and the cooperative bids.
Clicking a team in the legend toggles it in every panel. Two CSV tables next
to the page hold every plotted series. With --watch the page is rewritten and
reloads itself, so a running competition can be followed (app_state.json is
written after every step). Needs plotly: pip install .[plot]
"""

import argparse
import csv
import json
import math
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

from hackathon_backend.general_demand import demand_per_actor_profile
from hackathon_backend.market.tender import (
    COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW,
    PLAIN_MINIMUM_ORDER_AMOUNT_KW,
    cooperative_minimum_order_amount_kw,
    plain_minimum_order_amount_kw,
    supply_step_from_time,
)
from hackathon_backend.profiles import DEMAND_PER_ACTOR_KW
from hackathon_backend.units.pv import create_pv_unit
from hackathon_backend.units.unit import UnitInput
from hackathon_backend.units.weather import clear_sky_profile

STEP_S = 900
STEPS_PER_HOUR = 4
# a bar spans its quarter-hour from the interval start, like the step lines
BAR_OFFSET_H = 0.015
BAR_WIDTH_H = 0.22
# fixed categorical order (validated for adjacent pairs); a team past the
# eighth gets the neutral color instead of a generated hue
TEAM_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER_TEAM_COLOR = "#8a8984"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GREY = "#9a9893"
GREY_LIGHT = "#e4e2dc"
GRID = "#ecebe7"
SURFACE = "#fcfcfb"
FONT = 'Inter, "Segoe UI", system-ui, -apple-system, Helvetica, Arial, sans-serif'

PANELS = [
    ("system", "System: demand against what all teams together can supply (kW) · shaded: batteries needed"),
    ("plan", "Perfect-foresight plan: how the demand can be served (kW), with the delivered power"),
    ("storage", "Energy stored in all batteries (kWh): plan and actual"),
    ("market", "Market: offered, demand, minimum order and delivered power (kW)"),
    ("share", "Share of the demand served so far (%)"),
    ("prices", "Order prices (ct/kW) · filled: awarded, open: rejected, size: amount"),
    ("awarded", "Awarded power by team (kW), demand as line"),
    ("delivered", "Delivered net power by team (kW), below 0: drawn from the grid"),
    ("units", "PV generation and load of a team (kW)"),
    ("battery", "Battery power by team (kW), above 0: charging"),
    ("soc", "Battery charge by team (%)"),
    ("balance", "Balance by team (ct)"),
    ("coop", "Cooperative bids by supply time"),
]
NOT_SERVABLE_COLOR = "#d03b3b"  # status critical
BATTERY_NEEDED_TINT = "rgba(42, 120, 214, 0.07)"
COOP_STATUS_COLORS = {
    "closed": INK_SECONDARY,
    "expired": GREY,
    "withdrawn": GREY_LIGHT,
    "open": "#c9c7c1",
}
COOP_STATUS_NAMES = {"closed": "filled", "expired": "expired", "withdrawn": "withdrawn", "open": "open"}


def hour(step):
    return step / STEPS_PER_HOUR


def _columns_to_rows(payload):
    columns = json.loads(payload) if payload else {}
    if not columns:
        return []
    index = sorted(next(iter(columns.values())), key=int)
    return [{name: columns[name].get(i) for name in columns} for i in index]


class Situation:
    """The time series of a run, extracted from its persisted state."""

    def __init__(self, state: dict):
        self.state = state
        self.step = state["step"]
        self.pv_seed = state.get("pv_seed")
        self.config = state["config"]
        names = state.get("participant_names", {})
        self.teams = {
            actor_id: names.get(participant_id, participant_id[:-2])
            for actor_id, participant_id in state.get("actor_to_participant", {}).items()
        }
        for actor_id in state["actor_account_data"]:
            self.teams.setdefault(actor_id, actor_id[:8])
        self.colors = {
            actor_id: TEAM_COLORS[i] if i < len(TEAM_COLORS) else OTHER_TEAM_COLOR
            for i, actor_id in enumerate(self.teams)
        }

        auctions = {}
        for auction in state["market"]["expired_auctions"] + list(state["market"]["auctions"].values()):
            auctions[auction["id"]] = auction
        self.market = {}
        self.orders = []
        self.awards = defaultdict(float)
        for auction in auctions.values():
            params = auction["params"]
            supply = int(params["supply_start_time"] // STEP_S)
            result = auction.get("result") or {}
            awarded = {}
            for order in result.get("awarded_orders", []):
                key = (tuple(order["agents"]), order["price_ct"], tuple(order["amount_kw"]))
                awarded[key] = order["awarded_amount_kw"]
                for agent, kw in zip(order["agents"], order["awarded_amount_kw"]):
                    self.awards[(supply, agent)] += kw
            offered = 0.0
            for order in auction["orders"]:
                amount = sum(order["amount_kw"])
                offered += amount
                awarded_kw = awarded.get(
                    (tuple(order["agents"]), order["price_ct"], tuple(order["amount_kw"]))
                )
                self.orders.append({
                    "supply_step": supply,
                    "agents": order["agents"],
                    "price_ct": order["price_ct"],
                    "amount_kw": amount,
                    "awarded_kw": sum(awarded_kw) if awarded_kw else 0.0,
                })
            self.market[supply] = {
                "supply_step": supply,
                "status": auction["status"],
                "tender_kw": params["tender_amount_kw"],
                "demand_kw": params["tender_amount_kw"],
                "minimum_kw": params["minimum_order_amount_kw"],
                "maximum_price_ct": params["maximum_price_ct"],
                "offered_kw": offered,
                "awarded_kw": sum(sum(o["awarded_amount_kw"]) for o in result.get("awarded_orders", [])),
                "clearing_price_ct": result.get("clearing_price"),
                "orders": len(auction["orders"]),
            }

        # the general demand has one row per unit step, which settles the
        # auction supplying at that step
        for step, row in enumerate(_columns_to_rows(state.get("general_demand_supply"))):
            entry = self.market.setdefault(step, {"supply_step": step})
            entry["delivered_kw"] = row.get("provided_amount_kw")
            entry["share_served"] = row.get("provided_share_until")

        self.history = defaultdict(list)
        for row in state.get("actor_history", []):
            self.history[row["actor_id"]].append(row)
        for rows in self.history.values():
            rows.sort(key=lambda row: row["step"])

        self.coop = defaultdict(lambda: defaultdict(int))
        for bid in state.get("cooperative_bids", []):
            self.coop[int(bid["supply_time"] // STEP_S)][bid["status"]] += 1

    def team_name(self, actor_ids):
        return " + ".join(self.teams.get(a, a[:8]) for a in actor_ids)

    def market_rows(self):
        return [self.market[step] for step in sorted(self.market)]

    def clear_sky_pv_kw(self):
        # the PV plant of the run's teams (all alike) under a clear sky
        pv = next(
            (unit for payload in self.state["unit_pool"]["actor_to_root_payload"].values()
             for unit in json.loads(payload)["unit_information_list"] if "a_m2" in unit),
            {"a_m2": 12, "eta_percent": 25},
        )
        unit = create_pv_unit("pv", clear_sky_profile(), a_m2=pv["a_m2"], eta_percent=pv["eta_percent"])
        return [-unit.get_pv_power(UnitInput(STEP_S, 0, 0), s).p_kw for s in range(96)]


class SystemView:
    """The physics of all teams together for the whole day: their PV, load
    and batteries (from the persisted unit parameters and the weather of the
    run) against the demand and tender of every auction, and a plan with
    perfect foresight of how the demand can be served. The plan serves each
    step from PV first, then from the batteries, and charges them with any
    surplus; with lossless storage that serves as much as any schedule can.
    It ignores minimum order amounts and who cooperates with whom."""

    def __init__(self, state: dict):
        config = state["config"]
        self.steps = config["max_steps"]
        self.pv_kw = [0.0] * self.steps
        self.load_kw = [0.0] * self.steps
        self.capacity_kwh = self.charge_max_kw = self.discharge_max_kw = 0.0
        initial_soc = config.get("battery_initial_soc_percent", 50) / 100
        self.initial_kwh = 0.0
        pv_cache = {}
        payloads = state["unit_pool"]["actor_to_root_payload"]
        self.n_actors = len(payloads)
        self.capacity_by_actor = {}
        for actor_id, payload in payloads.items():
            for unit in json.loads(payload)["unit_information_list"]:
                if "soc_percent" in unit:
                    self.capacity_by_actor[actor_id] = unit["cap_kwh"]
                    self.capacity_kwh += unit["cap_kwh"]
                    self.charge_max_kw += unit["p_charge_max_kw"]
                    self.discharge_max_kw += unit["p_discharge_max_kw"]
                    self.initial_kwh += unit["cap_kwh"] * initial_soc
                elif "a_m2" in unit:
                    key = (tuple(unit["pv_p_kw"]), unit["a_m2"], unit["eta_percent"], unit["t_module_deg_celsius"])
                    if key not in pv_cache:
                        pv_unit = create_pv_unit(
                            "pv", unit["pv_p_kw"], a_m2=unit["a_m2"], eta_percent=unit["eta_percent"],
                            t_module_deg_celsius=unit["t_module_deg_celsius"],
                        )
                        n = len(unit["pv_p_kw"])
                        pv_cache[key] = [-pv_unit.get_pv_power(UnitInput(STEP_S, 0, 0), s % n).p_kw
                                         for s in range(self.steps)]
                    self.pv_kw = [a + b for a, b in zip(self.pv_kw, pv_cache[key])]
                elif "perfect_demand_p_kw" in unit:
                    profile = unit["perfect_demand_p_kw"]
                    self.load_kw = [a + profile[s % len(profile)] for s, a in enumerate(self.load_kw)]
        self.clear_sky_share = None
        if pv_cache:
            _, a_m2, eta_percent, _ = next(iter(pv_cache))
            clear = create_pv_unit("pv", clear_sky_profile(), a_m2=a_m2, eta_percent=eta_percent)
            clear_kwh = sum(-clear.get_pv_power(UnitInput(STEP_S, 0, 0), s % 96).p_kw for s in range(self.steps))
            first = next(iter(pv_cache.values()))
            self.clear_sky_share = sum(first) / clear_kwh if clear_kwh else None

        # demand, tender and minimum of the auction supplying at every step
        per_actor = demand_per_actor_profile(config.get("demand_per_actor_kw") or DEMAND_PER_ACTOR_KW)
        self.demand_kw = [0.0] * self.steps
        self.tender_kw = [0.0] * self.steps
        self.minimum_kw = [None] * self.steps
        for supply in range(5, self.steps):
            step_of_day = supply_step_from_time(supply * STEP_S)
            demand = round(max(1, self.n_actors) * per_actor[step_of_day], 1)
            if config.get("cooperative_bidding"):
                minimum = cooperative_minimum_order_amount_kw(
                    demand, config.get("cooperative_minimum_order_kw", COOPERATIVE_MINIMUM_ORDER_AMOUNT_KW)
                )
            else:
                minimum = plain_minimum_order_amount_kw(
                    demand, config.get("plain_minimum_order_kw", PLAIN_MINIMUM_ORDER_AMOUNT_KW)
                )
            self.demand_kw[supply] = demand
            self.tender_kw[supply] = demand
            self.minimum_kw[supply] = minimum

        self.plan = self.serve(self.demand_kw)
        self.margin = self._margin()

    @property
    def surplus_kw(self):
        return [pv - load for pv, load in zip(self.pv_kw, self.load_kw)]

    def serve(self, target_kw):
        """Serve the target with perfect foresight: per step the power from
        PV, from the batteries, what cannot be served, the own load which
        would have to be drawn from the grid, and the stored energy after the
        step."""
        dt_h = STEP_S / 3600
        stored = self.initial_kwh
        plan = []
        for net, target in zip(self.surplus_kw, target_kw):
            if net >= target:
                charge = min(net - target, self.charge_max_kw, (self.capacity_kwh - stored) / dt_h)
                stored += charge * dt_h
                row = {"from_pv_kw": target, "from_battery_kw": 0.0, "not_servable_kw": 0.0,
                       "grid_kw": 0.0, "battery_kw": charge}
            else:
                battery = min(target - net, self.discharge_max_kw, stored / dt_h)
                stored -= battery * dt_h
                from_pv = min(max(net, 0.0), target)
                served = min(max(net + battery, 0.0), target)
                row = {"from_pv_kw": from_pv, "from_battery_kw": served - from_pv,
                       "not_servable_kw": target - served, "grid_kw": max(0.0, -(net + battery)),
                       "battery_kw": -battery}
            row["stored_kwh"] = stored
            plan.append(row)
        return plan

    def _margin(self):
        """Largest factor m such that m times the demand is served at every
        step without grid draw."""
        def feasible(m):
            plan = self.serve([m * d for d in self.demand_kw])
            return all(r["not_servable_kw"] < 1e-6 and r["grid_kw"] < 1e-6 for r in plan)

        if not feasible(0.0):
            return 0.0
        low, high = 0.0, 1.0
        while feasible(high) and high < 64:
            low, high = high, high * 2
        for _ in range(30):
            middle = (low + high) / 2
            low, high = (middle, high) if feasible(middle) else (low, middle)
        return low

    def battery_needed_steps(self):
        """Steps whose demand exceeds what PV leaves after the own load."""
        return [
            s for s in range(self.steps)
            if self.demand_kw[s] > 0 and self.demand_kw[s] > self.surplus_kw[s] + 1e-9
        ]


def _runs(steps):
    """Contiguous runs of steps as (first, last) pairs."""
    runs = []
    for step in steps:
        if runs and step == runs[-1][1] + 1:
            runs[-1][1] = step
        else:
            runs.append([step, step])
    return runs


def _add_system_panels(fig, add, rows, situation, system):
    import plotly.graph_objects as go

    x = [hour(step) for step in range(system.steps)]
    supply = [step for step in range(system.steps) if system.tender_kw[step] > 0]
    x_supply = [hour(step) for step in supply]
    surplus = system.surplus_kw

    for first, last in _runs(system.battery_needed_steps()):
        fig.add_vrect(
            x0=hour(first), x1=hour(last + 1), fillcolor=BATTERY_NEEDED_TINT, line_width=0,
            layer="below", row=rows["system"], col=1, exclude_empty_subplots=False,
        )
    add(go.Scatter(
        x=[None], y=[None], mode="markers", name="batteries needed (demand above PV − load)",
        marker=dict(symbol="square", size=12, color="rgba(42, 120, 214, 0.2)"), legendgroup="needed",
    ), "system")
    add(go.Scatter(
        x=x, y=surplus, name="PV − load, all teams", mode="lines", line=dict(width=0, shape="hv"),
        fill="tozeroy", fillcolor="#d9d7d1", legendgroup="surplus",
    ), "system")
    add(go.Scatter(
        x=x, y=[value + system.discharge_max_kw for value in surplus],
        name="PV − load + full battery power", mode="lines",
        line=dict(color=INK_SECONDARY, width=1.5, shape="hv"), legendgroup="surplus-battery",
    ), "system")
    add(go.Scatter(
        x=x, y=system.pv_kw, name="PV, all teams", mode="lines",
        line=dict(color=INK_SECONDARY, width=1.5, dash="dot"), legendgroup="pv-all",
    ), "system")
    add(go.Scatter(
        x=x, y=system.load_kw, name="load, all teams", mode="lines",
        line=dict(color=GREY, width=1.5, dash="dot", shape="hv"), legendgroup="load-all",
    ), "system")
    add(go.Scatter(
        x=x_supply, y=[system.demand_kw[step] for step in supply], name="demand (= tender)", mode="lines",
        line=dict(color=INK, width=2.5, shape="hv"), legendgroup="demand",
    ), "system")

    plan = system.plan
    for key, name, color in (
        ("from_pv_kw", "served from PV", "#c9c7c1"),
        ("from_battery_kw", "served from batteries", INK_SECONDARY),
        ("not_servable_kw", "not servable", NOT_SERVABLE_COLOR),
    ):
        add(go.Scatter(
            x=x, y=[row[key] for row in plan], name=name, mode="lines", stackgroup="plan",
            line=dict(width=0, shape="hv"), fillcolor=color, legendgroup=key,
        ), "plan")
    if any(row["grid_kw"] > 1e-6 for row in plan):
        add(go.Scatter(
            x=x, y=[-row["grid_kw"] for row in plan], name="own load from the grid", mode="lines",
            line=dict(color=NOT_SERVABLE_COLOR, width=2, shape="hv"), legendgroup="grid",
        ), "plan")
    delivered = [row for row in situation.market_rows() if row.get("delivered_kw") is not None]
    add(go.Scatter(
        x=[hour(r["supply_step"]) for r in delivered], y=[r["delivered_kw"] for r in delivered],
        name="delivered (actual)", mode="lines", line=dict(color=INK, width=2, dash="dash", shape="hv"),
        legendgroup="delivered-actual",
    ), "plan")

    add(go.Scatter(
        x=x, y=[system.capacity_kwh] * len(x), name="battery capacity, all teams", mode="lines",
        line=dict(color=GREY, width=1.5), legendgroup="capacity",
    ), "storage")
    add(go.Scatter(
        x=[hour(step + 1) for step in range(system.steps)], y=[row["stored_kwh"] for row in plan],
        name="stored, plan", mode="lines", line=dict(color=INK, width=2), legendgroup="stored-plan",
    ), "storage")
    stored = defaultdict(float)
    for actor_id, history in situation.history.items():
        capacity = system.capacity_by_actor.get(actor_id, 0.0)
        for row in history:
            if row.get("soc_percent") is not None:
                stored[row["step"]] += row["soc_percent"] / 100 * capacity
    steps = sorted(stored)
    add(go.Scatter(
        x=[hour(step + 1) for step in steps], y=[stored[step] for step in steps], name="stored, actual",
        mode="lines", line=dict(color=INK_SECONDARY, width=2, dash="dash"), legendgroup="stored-actual",
    ), "storage")


def build_figure(situation: Situation, system: "SystemView" = None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    system = system or SystemView(situation.state)
    rows = {key: i + 1 for i, (key, _) in enumerate(PANELS)}
    fig = make_subplots(
        rows=len(PANELS),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.022,
        subplot_titles=[title for _, title in PANELS],
    )
    market = [row for row in situation.market_rows() if "tender_kw" in row]
    x_market = [hour(row["supply_step"]) for row in market]

    def add(trace, panel):
        fig.add_trace(trace, row=rows[panel], col=1)

    _add_system_panels(fig, add, rows, situation, system)

    # layered areas: offered (light) behind delivered (dark); bars would be
    # drawn beneath a filled area
    delivered = [row for row in situation.market_rows() if row.get("delivered_kw") is not None]
    add(go.Scatter(
        x=x_market, y=[r["offered_kw"] for r in market], name="offered", mode="lines",
        line=dict(width=0, shape="hv"), fill="tozeroy", fillcolor=GREY_LIGHT, legendgroup="offered",
    ), "market")
    add(go.Scatter(
        x=[hour(r["supply_step"]) for r in delivered], y=[r["delivered_kw"] for r in delivered],
        name="delivered", mode="lines", line=dict(width=0, shape="hv", color=INK_SECONDARY),
        fill="tozeroy", fillcolor="rgba(82, 81, 78, 0.55)", legendgroup="delivered",
    ), "market")
    add(go.Scatter(
        x=x_market, y=[r["minimum_kw"] for r in market], name="minimum order", mode="lines",
        line=dict(color=GREY, width=1.5, shape="hv", dash="dot"), legendgroup="minimum",
    ), "market")
    add(go.Scatter(
        x=x_market, y=[r["demand_kw"] for r in market], name="demand", mode="lines",
        line=dict(color=INK, width=2, shape="hv"), legendgroup="demand", showlegend=False,
    ), "market")

    shares = [row for row in situation.market_rows() if row.get("share_served") is not None]
    add(go.Scatter(
        x=[hour(r["supply_step"]) for r in shares], y=[100 * r["share_served"] for r in shares],
        name="share of the demand served", mode="lines", line=dict(color=INK, width=2),
        legendgroup="share",
    ), "share")

    team_orders = defaultdict(list)
    group_orders = []
    for order in situation.orders:
        if len(order["agents"]) == 1:
            team_orders[order["agents"][0]].append(order)
        else:
            group_orders.append(order)

    def order_markers(orders, color, name, legendgroup, symbol_open, symbol_filled):
        # at least 8 px, area grows with the amount
        return go.Scatter(
            x=[hour(o["supply_step"]) for o in orders],
            y=[o["price_ct"] for o in orders],
            mode="markers",
            name=name,
            legendgroup=legendgroup,
            showlegend=False,
            marker=dict(
                color=color,
                size=[8 + 4 * math.sqrt(o["amount_kw"]) for o in orders],
                symbol=[symbol_filled if o["awarded_kw"] > 1e-9 else symbol_open for o in orders],
                line=dict(width=1.5, color=color),
                opacity=0.85,
            ),
            customdata=[
                [situation.team_name(o["agents"]), o["amount_kw"], o["awarded_kw"]] for o in orders
            ],
            hovertemplate="%{customdata[0]}<br>%{y:.0f} ct/kW · %{customdata[1]:.2f} kW offered"
            " · %{customdata[2]:.2f} kW awarded<extra></extra>",
        )

    for actor_id, orders in team_orders.items():
        add(order_markers(orders, situation.colors[actor_id], situation.teams.get(actor_id, actor_id[:8]),
                          actor_id, "circle-open", "circle"), "prices")
    if group_orders:
        add(order_markers(group_orders, INK_SECONDARY, "cooperative orders", "cooperative-orders",
                          "diamond-open", "diamond"), "prices")
        fig.data[-1].showlegend = True
    add(go.Scatter(
        x=x_market, y=[r["clearing_price_ct"] for r in market], name="clearing price",
        mode="lines+markers", line=dict(color=INK, width=2), marker=dict(size=8, color=INK),
        legendgroup="clearing-price",
    ), "prices")

    steps = [hour(row["supply_step"]) for row in market]
    team_legend_shown = set()
    for actor_id, name in situation.teams.items():
        color = situation.colors[actor_id]
        legend = dict(legendgroup=actor_id, name=name, showlegend=actor_id not in team_legend_shown)
        team_legend_shown.add(actor_id)
        add(go.Bar(
            x=steps, y=[situation.awards.get((row["supply_step"], actor_id), 0.0) for row in market],
            marker=dict(color=color, line=dict(color=SURFACE, width=1)),
            width=BAR_WIDTH_H, offset=BAR_OFFSET_H, **legend,
        ), "awarded")
        legend["showlegend"] = False
        history = situation.history.get(actor_id, [])
        x_team = [hour(row["step"]) for row in history]
        add(go.Scatter(x=x_team, y=[r["delivered_kw"] for r in history], mode="lines",
                       line=dict(color=color, width=2, shape="hv"), **legend), "delivered")
        add(go.Scatter(x=x_team, y=[r["battery_kw"] for r in history], mode="lines",
                       line=dict(color=color, width=2, shape="hv"), **legend), "battery")
        add(go.Scatter(x=x_team, y=[r["soc_percent"] for r in history], mode="lines",
                       line=dict(color=color, width=2), **legend), "soc")
        add(go.Scatter(x=x_team, y=[r["balance_ct"] for r in history], mode="lines",
                       line=dict(color=color, width=2, shape="hv"), **legend), "balance")
    add(go.Scatter(
        x=x_market, y=[r["demand_kw"] for r in market], name="demand", mode="lines",
        line=dict(color=INK, width=2, shape="hv"), legendgroup="demand", showlegend=False,
    ), "awarded")

    # every team has the same units, so one PV and one load line describe all
    # of them; differing teams get a line each
    per_team_pv = {a: [r["pv_kw"] for r in h] for a, h in situation.history.items()}
    per_team_load = {a: [r["load_kw"] for r in h] for a, h in situation.history.items()}
    add(go.Scatter(
        x=[hour(s) for s in range(96)], y=situation.clear_sky_pv_kw(), name="PV on a clear day",
        mode="lines", line=dict(color=GREY, width=1.5), legendgroup="clear-sky",
    ), "units")
    for label, series, dash in (("PV", per_team_pv, "solid"), ("load", per_team_load, "dot")):
        distinct = {tuple(round(v, 6) for v in values) for values in series.values()}
        if len(distinct) <= 1:
            actor_id = next(iter(series), None)
            if actor_id is None:
                continue
            history = situation.history[actor_id]
            add(go.Scatter(
                x=[hour(r["step"]) for r in history], y=series[actor_id],
                name=f"{label} (every team)", mode="lines",
                line=dict(color=INK, width=2, dash=dash), legendgroup=label,
            ), "units")
        else:
            for actor_id, values in series.items():
                history = situation.history[actor_id]
                add(go.Scatter(
                    x=[hour(r["step"]) for r in history], y=values, mode="lines",
                    line=dict(color=situation.colors[actor_id], width=2, dash=dash),
                    name=f"{label} {situation.teams[actor_id]}", legendgroup=actor_id, showlegend=False,
                ), "units")

    for status in ("closed", "expired", "withdrawn", "open"):
        coop_steps = sorted(s for s, counts in situation.coop.items() if counts.get(status))
        if not coop_steps:
            continue
        add(go.Bar(
            x=[hour(s) for s in coop_steps], y=[situation.coop[s][status] for s in coop_steps],
            name=f"cooperative bids {COOP_STATUS_NAMES[status]}",
            marker=dict(color=COOP_STATUS_COLORS[status], line=dict(color=SURFACE, width=1)),
            width=BAR_WIDTH_H, offset=BAR_OFFSET_H, legendgroup=f"coop-{status}",
        ), "coop")

    fig.update_layout(
        barmode="relative",
        bargap=0.12,
        height=250 * len(PANELS),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, size=12, color=INK)),
        legend=dict(groupclick="togglegroup", tracegroupgap=14, bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=70, r=20, t=90, b=50),
        title=dict(text=_title(situation, system), x=0.01, font=dict(size=16)),
    )
    fig.update_annotations(font=dict(size=13, color=INK_SECONDARY), x=0, xanchor="left")
    fig.update_xaxes(
        range=[0, 24], dtick=2, ticksuffix=" h", showgrid=False, zeroline=False,
        showline=True, linecolor=GRID, showspikes=True, spikemode="across",
        spikethickness=1, spikecolor=GREY, spikedash="solid",
    )
    fig.update_xaxes(title_text="time of day (supply interval start)", row=len(PANELS), col=1)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=GRID, linecolor=GRID)
    fig.update_yaxes(range=[0, 105], row=rows["share"], col=1)
    fig.update_yaxes(range=[0, 105], row=rows["soc"], col=1)
    maximum_price = max((row["maximum_price_ct"] for row in market), default=1000)
    fig.update_yaxes(range=[0, maximum_price * 1.05], row=rows["prices"], col=1)
    return fig


def _title(situation, system):
    market = [row for row in situation.market_rows() if "tender_kw" in row]
    offered_kwh = sum(row["offered_kw"] for row in market) / STEPS_PER_HOUR
    delivered_kwh = sum(row.get("delivered_kw") or 0.0 for row in situation.market_rows()) / STEPS_PER_HOUR
    demand_kwh = sum(system.demand_kw) / STEPS_PER_HOUR
    auction_steps = sum(1 for tender in system.tender_kw if tender > 0)
    weather = (
        f" · PV {system.clear_sky_share:.0%} of a clear day" if system.clear_sky_share is not None else ""
    )
    return (
        f"Competition situation after step {situation.step} · {len(situation.teams)} teams · "
        f"weather seed {situation.pv_seed}{weather}<br><sup>With perfect foresight all teams together can "
        f"serve {system.margin:.2f} × the demand at every step · batteries are needed at "
        f"{len(system.battery_needed_steps())} of {auction_steps} auction steps · demand of the day "
        f"{demand_kwh:.1f} kWh · offered so far {offered_kwh:.1f} kWh · delivered so far "
        f"{delivered_kwh:.1f} kWh</sup>"
    )


def write_tables(situation: Situation, out: Path, system: "SystemView" = None):
    """market.csv, teams.csv and system.csv next to the page, every plotted
    series."""
    system = system or SystemView(situation.state)
    with open(out.with_name(out.stem + "-system.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "step", "hour", "pv_kw", "load_kw", "pv_minus_load_kw", "demand_kw", "minimum_kw", "battery_needed", "plan_from_pv_kw", "plan_from_battery_kw",
            "plan_not_servable_kw", "plan_grid_kw", "plan_stored_kwh",
        ])
        needed = set(system.battery_needed_steps())
        for step, row in enumerate(system.plan):
            writer.writerow([
                step, hour(step), system.pv_kw[step], system.load_kw[step], system.surplus_kw[step],
                system.demand_kw[step], system.minimum_kw[step], step in needed,
                row["from_pv_kw"], row["from_battery_kw"], row["not_servable_kw"], row["grid_kw"],
                row["stored_kwh"],
            ])
    market_columns = [
        "supply_step", "status", "tender_kw", "minimum_kw",
        "maximum_price_ct", "offered_kw", "awarded_kw",
        "clearing_price_ct", "orders", "delivered_kw", "share_served",
    ]
    with open(out.with_name(out.stem + "-market.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["hour"] + market_columns, extrasaction="ignore")
        writer.writeheader()
        for row in situation.market_rows():
            writer.writerow({"hour": hour(row["supply_step"]), **row})
    team_columns = [
        "team", "actor_id", "step", "hour", "awarded_kw", "delivered_kw", "pv_kw", "load_kw",
        "battery_kw", "soc_percent", "payoff_ct", "grid_penalty_ct", "balance_ct",
    ]
    with open(out.with_name(out.stem + "-teams.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=team_columns, extrasaction="ignore")
        writer.writeheader()
        for actor_id, rows in situation.history.items():
            for row in rows:
                writer.writerow({"team": situation.teams.get(actor_id), "hour": hour(row["step"]), **row})


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>Competition situation</title>
<style>body {{ margin: 0; padding: 8px 16px; background: {surface}; }}</style>
</head>
<body>
{body}
</body>
</html>
"""


def render(state_file: Path, out: Path, refresh_s=None):
    from plotly.offline import get_plotlyjs

    situation = Situation(json.loads(state_file.read_text(encoding="utf-8")))
    system = SystemView(situation.state)
    figure = build_figure(situation, system)
    if refresh_s:
        # the page reloads itself; the library is written once next to it
        library = out.with_name("plotly.min.js")
        if not library.exists():
            library.write_text(get_plotlyjs(), encoding="utf-8")
        body = figure.to_html(full_html=False, include_plotlyjs="plotly.min.js")
        refresh = f'<meta http-equiv="refresh" content="{int(refresh_s)}">\n'
    else:
        body = figure.to_html(full_html=False, include_plotlyjs=True)
        refresh = ""
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(PAGE.format(refresh=refresh, surface=SURFACE, body=body), encoding="utf-8")
    tmp.replace(out)
    write_tables(situation, out, system)
    return situation


def main():
    parser = argparse.ArgumentParser(prog="python -m hackathon_backend.plot", description=__doc__.split("\n\n")[0])
    parser.add_argument("--state", type=Path, default=Path("app_state.json"))
    parser.add_argument("--out", type=Path, default=Path("situation.html"))
    parser.add_argument("--watch", type=float, metavar="SECONDS",
                        help="rewrite the page every SECONDS; the page reloads itself")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()
    try:
        import plotly  # noqa: F401
    except ImportError:
        raise SystemExit("The plotter needs plotly: pip install .[plot]")

    if not args.watch:
        situation = render(args.state, args.out)
        print(f"wrote {args.out} (step {situation.step}, {len(situation.teams)} teams)")
        if args.open:
            webbrowser.open(args.out.resolve().as_uri())
        return
    opened = False
    while True:
        try:
            situation = render(args.state, args.out, args.watch)
            print(f"wrote {args.out} (step {situation.step}, {len(situation.teams)} teams)")
            if args.open and not opened:
                webbrowser.open(args.out.resolve().as_uri())
                opened = True
        except (OSError, ValueError, KeyError) as e:
            # no state before the first step; it may also be from an older backend
            print(f"waiting for a readable state: {e!r}")
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
