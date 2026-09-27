# Getting Started

This is the backend of a market and unit simulation environment. This environment simulates a simple market and the units of all market participants.

## Install dependencies
```bash
pip install .
```

## Start server (might need to used chmod +x before)
```bash
uvicorn hackathon_backend.main:app --host 0.0.0.0 --port 8000 --reload --log-config=log_conf.yaml
```

After the server has been started you can check the REST APIs under (default): http://localhost:8000/docs.

## Config
There is a configuration file for the backend config.json. This files include several options:
* participants: List of participants, which will be accepted on register (optional when the backend issues registration keys, see below). An entry is either `{"name": "Team A", "key": "<secret>"}` (the name is shown, the key is secret) or a legacy participant id string, which is its own key and is shown without its last two characters, so all but those two characters are public
* rt_step_duration_s: Real-time duration per simulated time step
* rt_step_init_delay_s: Real-time delay before the first step is simulated
* pause: True if you want to pause the server (no restart required)
* max_steps: Number of time steps to simulate
* test_mode: Special test mode, this will allow to call register multiple times for the same participant, and it will allow registration for the whole duration

* issue_registration_keys: Let participants fetch their own registration key from the backend (default false, see below)
* max_keys_per_ip: Number of registration keys handed out per client IP address (default 1)

* cooperative_bidding: Enable cooperative bidding (default false, see below)

* grid_penalty_ct_per_kw: Penalty per kW of net power drawn from the grid per quarter-hour (default 1500; keep it above maximum_price_ct, so selling the energy for the own load and drawing it from the grid again never pays)
* pv_seed: Seed of the PV weather of the day (default null: a new day every run, see below)
* actor_load_kw: Constant own load of every actor in kW (default null: the household profile, see below)
* battery_initial_soc_percent: Charge of every battery at the start of the day (default 65)
* admin_token: Token for the admin endpoints (default null: the admin endpoints are disabled)

The shape of the day and the market rules (defaults in brackets; `config.json` lists them all with these values). Unit parameters apply to actors registering afterwards, market values to every new auction:
* household_load_kw: Own load of every actor in kW, 24 values for 00:00 .. 23:00, linear in between (the household profile of `hackathon_backend/profiles.py`); `actor_load_kw` replaces it by a constant
* demand_per_actor_kw: Market demand per registered actor in kW, 24 hourly values like above; the tender of an auction is the number of registered actors times this, rounded to 0.1 kW (`hackathon_backend/profiles.py`)
* pv_peak_kw: Nominal peak of every PV plant (3)
* battery_capacity_kwh, battery_charge_max_kw, battery_discharge_max_kw: Battery of every actor (12, 2, 2)
* maximum_price_ct: Price cap per kW and quarter-hour of every auction (1000)
* shortfall_penalty_ct_per_kw: Awarded but not delivered power costs the order price, but at least this per kW (250)
* cooperative_minimum_order_kw: Minimum order amount with cooperative bidding, never more than the tender (1.8)
* plain_minimum_order_kw: Minimum order amount without cooperative bidding, never more than the tender (0.1, the resolution of the tender: no real minimum)

The design checks (`tests/test_supply_margin.py`, `python -m hackathon_backend.design_check`) hold for these defaults; after changing them, run `python -m hackathon_backend.design_check --config config.json` (the default), which builds the day from the config.

The config file is re-read every step, so options can be changed at runtime. If the file cannot be read (e.g. while an editor saves it), the last good config is kept and the simulation keeps stepping.

## Registration keys and authentication

The key a participant registers with is its secret. It is needed on every request that acts for an actor or reads its private state, as the query parameter `key` next to the `actor_id`:

* `POST /market/auction/order`, `POST /market/cooperative/propose`, `/join` and `/withdraw`
* `GET /units/information`, `GET /market/auction/result`, `GET /market/cooperative/mine`

The `actor_id` itself is public (it appears in the balances and in the cooperative bids) and grants nothing. A missing key gives 422, a key which does not belong to the actor 403, an unknown actor 404. `GET /ui/participant_map` maps every actor id to the name shown in the ranking and contains no key material.

With `"issue_registration_keys": true` participants get their key from the backend instead of the organizers:

1. `POST /hackathon/key?name=<team name>` -> `{"key": "..."}`. The name (1 to 32 characters, unique ignoring case) is what the scoreboard shows.
2. `POST /hackathon/register?participant_id=<key>` registers as before. Keep the key, it is only handed out once.

Every client IP address gets at most `max_keys_per_ip` keys (409 afterwards), and no keys are issued once registration closed (405). The limit is per IP and not per MAC address, because the MAC address of a client never reaches the server. Participants behind one NAT share a public address, so host the backend in the participants' network or raise `max_keys_per_ip`. Behind a reverse proxy the address is taken from `X-Forwarded-For` only if the proxy is trusted by uvicorn (`--forwarded-allow-ips`, default `127.0.0.1`).

Issued keys are stored in `registration_keys.json` (key, name, IP, time) and survive restarts. To hand out a key again or revoke one, edit that file and restart the server. The participants in `participants` keep working alongside.

## Units and PV weather

Every actor gets the same units: a household load (0.5 kW at night, peaks of 1.0 kW in the morning and in the evening, 16.7 kWh a day, see `hackathon_backend/profiles.py`; a constant load with `actor_load_kw`), a PV plant (3 kW nominal peak) and a 12 kWh / 2 kW battery which starts at 65 % (`battery_initial_soc_percent`) and balances the actor's net output against its awarded power.

The PV irradiance of the day is the clear-sky cosine profile times a cloud factor (a daily clearness of 0.85 .. 1.0 plus a smooth AR(1) cloud process, clipped to 0.3 .. 1.0, see `hackathon_backend/units/weather.py`). Every morning has fog, 20 .. 40 % deep, until a random time between 07:00 and 09:00, clearing within an hour. A dark day is brightened, keeping the shape of its clouds, until it yields at least 85 % of the clear-sky energy. A new day is drawn every run and shared by all actors, so nothing learned in a previous run transfers; `pv_seed` fixes the day for reproducible runs. The seed is persisted, so a reload keeps the day.

`GET /units/information` returns 9 values per forecast, the first for the interval which just ended (measured), the following ones forecasts. The PV forecast k steps ahead is `actual * (1 + 0.03 * k * z)` with `z = 0.7 * z_target + sqrt(1 - 0.7^2) * z_issue` (both `N(0, 1)`, clipped to ±4 only): unbiased, growing with the lead time (about 9 .. 18 % for the auctions open at a time), fixed per issue step and target step (repeated reads return the same numbers), correlated between the forecasts of one target step (averaging them removes little) and identical for all actors. There is no exact lower bound, so planning a delivery stays a risk decision. The load is forecast exactly.

## Supply margin

The market demand per registered actor follows a household shape too: 0.35 kW at night and at midday, peaks of 0.6 kW in the morning (07:00 .. 08:00) and 0.7 kW in the evening (19:00), 11.1 kWh a day (`hackathon_backend/profiles.py`). The tender of every auction is exactly this demand, `round(n_registered * demand_per_actor_kw, 1)`; nothing is added on top. The day is tuned so that the prices matter, checked with perfect foresight on 300 weather days (`tests/test_supply_margin.py`, the plotter shows the same for a run):
- the own load is always coverable without selling anything, and the battery never needs more than 3.3 kWh (28 %) for it, so most of it is free for trading
- all actors together can serve 1.75 times the demand at every step on the worst day (median 1.78, clear sky 2.24), so the demand is comfortably reachable and not every order is awarded
- the start charge of the batteries sets the margin of the night and the morning: every kWh less at the start is one kWh less PV lost at midday (the batteries are full by then anyway), but a tighter night. At 65 % the margin is 1.75 at worst; at 70 % 1.80 with more PV lost, at 60 % 1.63, at 50 % 1.24
- power is sparse in the morning (fog, batteries low after the night, demand peak) and in the evening (PV gone, demand and load peak): the demand of these 2 h blocks alone could rise only 2.3 .. 3.4 times; at midday it could rise 7 .. 10 times, so there prices must fall. Real strategies reach only about half of these values (reserves, forecast risk, cooperation), so the sparse blocks are just contested in practice; with 1.6 .. 1.8 the agent tests found them undersupplied, and bidding the price cap paid again

Supply and demand both scale with the number of actors, so these margins hold for every field size; the design is made for 6 to 15 teams (tender 2.1 .. 4.2 kW for 6 teams, 5.2 .. 10.5 kW for 15). `python -m hackathon_backend.design_check` (needs `pip install .[plot]`) renders the check as `design_check.html`.

`GET /system/demand` returns per supply step the tender, the provided power and the share of the tender served so far.

## Clearing and penalties

Every auction buys `tender_amount_kw` for one quarter-hour. At gate closure the orders are awarded cheapest first (pay-as-bid: every awarded order is paid its own price). Orders at the same price form a level; a level which does not fit completely is shared equally by its bidders (an actor, or the members of a group order), each capped at what it offered, so neither the arrival order nor splitting or inflating orders gains anything (a bidder's share is split over its orders by amount, a group order's by its members' amounts). The clearing price is the price of the last level which received power.

Payoff per quarter-hour and actor: price × delivered kW for every awarded order; the power the actor delivers fills its orders cheapest first, and every missing kW costs the order price, but at least 250 ct. Net power drawn from the grid costs `grid_penalty_ct_per_kw` (default 1500 ct) per kW, more than the price cap, so selling the energy for the own load and importing it never pays.

Auctions are only created while their supply starts before `max_steps * 900`, so every auction is settled.

## Cooperative bidding

With `"cooperative_bidding": true` every auction gets a minimum order amount of 1.8 kW (see `hackathon_backend/market/tender.py`):

```
tender_amount_kw        = round(n_registered * demand_per_actor_kw, 1)   # the demand, as in the plain mode
minimum_order_amount_kw = min(1.8, tender_amount_kw)
```

Rationale: at night and in the late evening an actor's default units can deliver at most 1.4 .. 1.8 kW net (battery 2 kW minus the load, plus the last PV), which is below the minimum, so these orders need a cooperative bid, while two actors have plenty of slack. Measured with the unit models the minimum is reachable alone from 03:00 to 19:30 on a clear day (steps 12..77), and with the weather of a run from about 04:00 (03:15 .. 05:00) to 18:30 .. 19:30, so roughly from 19:30 to 04:00 only cooperative bids can sell. The minimum does not scale with the number of participants, because it is defined relative to what one actor can supply. From 6 actors on the smallest tender (6 * 0.35 kW = 2.1 kW) holds a minimum-sized order at every step; smaller fields still get an order in, because the minimum is capped at the tender.

With the option disabled there is no minimum order amount beyond the 0.1 kW resolution of the tender (`plain_minimum_order_kw`): every actor sells alone, any amount up to the tender. An order is always placed by one actor; power can only be pooled in cooperative bids.

Flow of a cooperative bid (`hackathon_backend/market/cooperative.py`):

1. An actor proposes a cooperative bid for an open auction with its own amount, ONE common price for the whole bid and an optional target amount (default: the minimum order amount of the auction; it must lie between the minimum order amount and the tender amount). The proposer's amount must be below the target (otherwise place a normal order). The proposer is the first member.
2. Other actors see the open bids via `GET /market/cooperative/open` and join with their own amount. The accepted amount is capped to the remaining amount of the bid. A member (the proposer included) tops up its amount by joining again; every amount put in is at least 0.1 kW, unless less remains.
3. A member may withdraw from an open bid: its share is removed and the bid stays open. If the proposer withdraws, the whole bid is cancelled (status "withdrawn").
4. As soon as the target is reached the bid closes automatically and the group order (all members as agents with their amounts, at the common price) is placed in the auction immediately.
5. The awarded group order is credited to every member's OWN account by its awarded share, i.e. the value of the bid is distributed by the power amount each member put into it. There is no joint account.
6. Bids which did not reach their target until the gate closure of their auction expire and are never submitted.

Limits per actor and auction: at most 5 open proposals, and at most 10 orders (solo and group orders), where every open cooperative bid the actor is a member of reserves one of the 10. The limit is checked when an order is placed, a bid is proposed or joined by a new member (400 afterwards), so a filled bid can always be placed.

Endpoints (query parameters; every cooperative endpoint returns 403 while the option is disabled, except `/ui/cooperative`; a wrong `key` gives 403):

* `POST /market/cooperative/propose` with `actor_id`, `key`, `amount_kw`, `price_ct`, `supply_time` and optionally `target_amount_kw` -> `{"cooperative_bid": {...}}`. 404 for an unknown actor or without an open auction for the supply time, 400 for invalid (non-positive, NaN or infinite) amounts, prices or targets, or when the actor has no order slot left. The price is capped to the maximum price of the auction.
* `POST /market/cooperative/join` with `actor_id`, `key`, `cooperative_bid_id`, `amount_kw` -> `{"cooperative_bid": {...}, "accepted_amount_kw": ...}`. A member joining again tops up its amount. 404 for an unknown actor or bid, 409 if the bid is not open (or its auction closed meanwhile), 400 if the amount is not positive (or not finite) or a new member has no order slot left.
* `POST /market/cooperative/withdraw` with `actor_id`, `key`, `cooperative_bid_id` -> `{"cooperative_bid": {...}}`. 404 for an unknown actor or bid, 409 if the bid is not open, 400 if the actor is not a member.
* `GET /market/cooperative/open` with optional `supply_time` -> `{"cooperative_bids": [...]}`, only open bids sorted by supply time and creation time.
* `GET /market/cooperative/mine` with `actor_id`, `key` -> `{"cooperative_bids": [...]}`, bids of every status in which the actor is a member.
* `GET /ui/cooperative` -> `{"enabled": bool, "cooperative_bids": [...]}`, all bids of every status, newest first (at most 50); `{"enabled": false, "cooperative_bids": []}` while the option is disabled.

A cooperative bid is returned as `{"id", "auction_id", "supply_time", "product_type", "price_ct", "target_amount_kw", "filled_amount_kw", "remaining_amount_kw", "status" ("open" | "closed" | "expired" | "withdrawn"), "order_placed", "created_time", "members": [{"actor_id", "amount_kw"}]}`. The bids are part of the persisted state (`app_state.json`).

## End of the day: dispatch vs. the central optimum

`GET /ui/status` -> `{"step", "max_steps", "finished"}`; the day is finished once `step` reaches `max_steps`.

`GET /ui/dispatch` compares what all teams did so far with the central optimum of the same steps (`hackathon_backend/optimum.py`): one planner who knows the PV and load of every team for the whole day schedules every battery. The batteries are lossless, so this is a linear program (solved with HiGHS, once per step). The optimum serves the market demand first and then loses as little energy as possible; like in the backend, a battery only charges from the PV surplus and a team cannot draw from the grid and sell in the same step. Both sides get the same energy balance: start charge + PV − own loads + grid draw − market demand served − lost = left in the batteries, where lost is PV surplus that no battery stored and nobody bought (for the teams: power delivered beyond their awards). The response holds per step the market demand and, for `actual` and `optimum`, the served, unserved, grid, lost and battery power and the stored energy, plus the totals in kWh. The dashboard shows it after the podium at the end of the day.

## Plotting a run

`python -m hackathon_backend.plot` (needs `pip install .[plot]`) turns `app_state.json` into one interactive page, `situation.html`, with stacked panels on a shared time axis.

The system view shows whether the demand can be served and when batteries are needed:
- PV, load, PV − load and PV − load plus full battery power of all teams together, against the demand (= tender) of the day; steps whose demand exceeds PV − load are shaded (batteries needed)
- a perfect-foresight plan of how the demand is served (from PV, from batteries, not servable), next to the delivered power
- the energy stored in all batteries, planned and actual

The title states the margin: how many times the demand all teams together could serve at every step with perfect foresight, ignoring minimum orders and who cooperates with whom.

The market and team panels follow:
- the market: offered power, demand, minimum order amount and delivered power
- the share of the demand served
- every order price with the clearing price
- per team: awarded and delivered power, PV and load, battery power and charge, balance
- the cooperative bids by status

Clicking a team in the legend toggles it in every panel. `situation-system.csv`, `situation-market.csv` and `situation-teams.csv` next to the page hold every plotted series. The state records one row per team and step for this: awarded and delivered power, PV, load, battery power and charge, payoff, grid penalty and balance.

```bash
python -m hackathon_backend.plot --state app_state.json --out situation.html --open
python -m hackathon_backend.plot --watch 10    # rewrites the page every 10 s, the page reloads itself
```

## Admin

`POST /admin/load?admin_token=<token>` replaces the running simulation with the state in `app_state.json` (written after every step) and continues stepping from its step, e.g. after a crash. It returns 403 while no `admin_token` is configured or for a wrong token, and 409 if the state cannot be loaded.
