"""The central optimum of a run: the dispatch one planner with perfect
foresight would choose for all teams together.

The planner knows the PV and load of every team for the whole day and
schedules every battery. Per team i and step t:

    battery + delivered + lost - grid = pv - load
    stored[t] = stored[t-1] + 0.25 h * battery,   0 <= stored <= capacity
    sum_i delivered + unserved = market demand (the tender of the step)

"lost" is PV surplus that no battery stores and nobody buys. The planner
serves the market demand first, then loses as little energy as possible,
and among equal schedules keeps the batteries as full as possible at every
step. Only the net power of a team is metered, so it cannot draw from the
grid and sell in the same step: a kW from the grid weighs more than a kW of
unserved demand, so the grid only ever covers an own load the batteries
cannot. Like in the backend, a battery only charges from the PV surplus.
The batteries are lossless (units/battery.py), so this is a linear program;
it needs no order rules, because every team can join a cooperative bid with
any amount from 0.1 kW on.
"""

import highspy
import numpy as np

DT_H = 0.25
UNSERVED_WEIGHT = 1000.0
GRID_WEIGHT = 2000.0
LOST_WEIGHT = 1.0
# far too small to trade against any of the weights above
TIE_BREAK = 1e-4


def central_optimum(pv_kw, load_kw, active, cap_kwh, charge_max_kw, discharge_max_kw, initial_kwh, demand_kw):
    """pv_kw, load_kw and active are (teams, steps) arrays, the battery
    parameters (teams,) arrays and demand_kw a (steps,) array. A team is not
    active before it registered. Returns the totals of all teams per step."""
    pv = np.asarray(pv_kw, dtype=float) * active
    load = np.asarray(load_kw, dtype=float) * active
    active = np.asarray(active, dtype=bool)
    demand = np.asarray(demand_kw, dtype=float)
    n_teams, n_steps = pv.shape
    nt = n_teams * n_steps
    battery, stored, delivered, lost, grid = (np.arange(nt) + k * nt for k in range(5))
    unserved = 5 * nt + np.arange(n_steps)
    n_cols = 5 * nt + n_steps

    team = np.repeat(np.arange(n_teams), n_steps)
    step = np.tile(np.arange(n_steps), n_teams)
    balance_rows = np.arange(nt)
    storage_rows = nt + np.arange(nt)
    market_rows = 2 * nt + step
    later = step > 0
    rows = np.concatenate([
        balance_rows, balance_rows, balance_rows, balance_rows,
        storage_rows, storage_rows[later], storage_rows,
        market_rows, 2 * nt + np.arange(n_steps),
    ])
    cols = np.concatenate([
        battery, delivered, lost, grid,
        stored, stored[later] - 1, battery,
        delivered, unserved,
    ])
    values = np.concatenate([
        np.ones(3 * nt), -np.ones(nt),
        np.ones(nt), -np.ones(later.sum()), np.full(nt, -DT_H),
        np.ones(nt + n_steps),
    ])
    rhs = np.concatenate([
        (pv - load).ravel(),
        np.where(step == 0, np.asarray(initial_kwh, dtype=float)[team], 0.0),
        demand,
    ])

    on = active.ravel()
    inf = highspy.kHighsInf
    lower = np.concatenate([
        np.where(on, -np.asarray(discharge_max_kw, dtype=float)[team], 0.0),
        np.zeros(4 * nt + n_steps),
    ])
    upper = np.concatenate([
        np.where(on, np.minimum(np.asarray(charge_max_kw, dtype=float)[team],
                                np.maximum(pv - load, 0.0).ravel()), 0.0),
        np.asarray(cap_kwh, dtype=float)[team],
        np.where(on, inf, 0.0),
        pv.ravel(),
        np.where(on, inf, 0.0),
        demand,
    ])
    cost = np.zeros(n_cols)
    cost[unserved] = UNSERVED_WEIGHT
    cost[grid] = GRID_WEIGHT
    cost[lost] = LOST_WEIGHT
    cost[stored] = -TIE_BREAK

    order = np.lexsort((rows, cols))
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = n_cols, 2 * nt + n_steps
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = cost, lower, upper
    lp.row_lower_ = lp.row_upper_ = rhs
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = np.searchsorted(cols[order], np.arange(n_cols + 1))
    lp.a_matrix_.index_ = rows[order]
    lp.a_matrix_.value_ = values[order]
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.passModel(lp)
    solver.run()
    status = solver.getModelStatus()
    if status != highspy.HighsModelStatus.kOptimal:
        raise RuntimeError(f"The central optimum could not be solved: {solver.modelStatusToString(status)}")
    x = np.array(solver.getSolution().col_value)

    def per_step(columns):
        return x[columns].reshape(n_teams, n_steps).sum(axis=0)

    return {
        "served_kw": per_step(delivered),
        "unserved_kw": x[unserved],
        "grid_kw": per_step(grid),
        "lost_kw": per_step(lost),
        "battery_kw": per_step(battery),
        "stored_kwh": per_step(stored),
    }
