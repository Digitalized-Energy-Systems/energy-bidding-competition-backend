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
* participants: List of participant IDs, which will be accepted on register
* rt_step_duration_s: Real-time duration per simulated time step
* rt_step_init_delay_s: Real-time delay before the first step is simulated
* pause: True if you want to pause the server (no restart required)
* max_steps: Number of time steps to simulate
* test_mode: Special test mode, this will allow to call register multiple times for the same participant, and it will allow registration for the whole duration

* cooperative_bidding: Enable cooperative bidding (default false, see below). The config file is re-read every step, so the option can be flipped at runtime
* cooperative_min_order_share: The minimum order amount is at most this share of the tender amount, 0 < share <= 1 (default 0.5, i.e. at least two minimum-sized bids fit into every auction)

## Cooperative bidding

With `"cooperative_bidding": true` every auction gets a minimum order amount which follows a profile of the supply step of the day (`supply_step = int(supply_start_time // 900) % 96`, see `hackathon_backend/market/tender.py`), and the tender is floored so that the minimum is at most `cooperative_min_order_share` of it:

```
minimum_order_amount_kw = round(2.5 - 0.5 * cos(2 * pi * supply_step / 96), 1)   # 2.0 kW at midnight, 3.0 kW at noon
tender_amount_kw        = round(max(n_registered * general_demand_kw, minimum_order_amount_kw / cooperative_min_order_share), 1)
```

Rationale: an actor's default units (1 kW load, PV with 3 kW peak on a cosine irradiance, 2 kW / 12 kWh battery) can supply roughly `2.5 - 1.5 * cos(2 * pi * step / 96)` kW, i.e. 1 kW at midnight and 4 kW at noon. By these formulas the minimum is reachable by a single actor exactly while `cos <= 0`, from 06:00 to 18:00 (about half of the 96 steps); during the other half of the day actors have to cooperate to place an order at all. The capacity formula overestimates the default units by about 6 % near that boundary (the PV model delivers a little less than its nominal peak), so measured with the unit models the minimum is reachable alone from about 06:45 to 17:15 (steps 27..69, 43 of 96 steps); a solo order of exactly the minimum at 06:00 or 18:00 under-delivers slightly and is penalized. The minimum does not scale with the number of participants, because it is defined relative to what one actor can supply.

The tender scales with the number of registered actors exactly like in the plain mode (`n_registered` times the general demand of 0.3 .. 0.5 kW per actor), so it fits the generation and flexibility the participants bring, but it is at least `minimum / cooperative_min_order_share` (4.0 .. 6.0 kW at the default share of 0.5), so at least two minimum-sized bids fit into every auction and a single bid never takes the whole tender. With the default share the floor is active all day for up to 10 actors (the plain tender of 10 actors is 3.0 .. 5.0 kW), partly for 11 to 15 actors, and from 16 actors on the tender is identical to the plain mode (6.0 .. 10.0 kW with 20 actors, i.e. room for three minimum-sized bids); the number of bids which fit therefore grows with the field. With the option disabled the behaviour is unchanged (tender = `max(1, n_registered)` * general demand, minimum order amount 1 kW).

Flow of a cooperative bid (`hackathon_backend/market/cooperative.py`):

1. An actor proposes a cooperative bid for an open auction with its own amount, ONE common price for the whole bid and an optional target amount (default: the minimum order amount of the auction; it must lie between the minimum order amount and the tender amount). The proposer's amount must be below the target (otherwise place a normal order). The proposer is the first member.
2. Other actors see the open bids via `GET /market/cooperative/open` and join with their own amount. The accepted amount is capped to the remaining amount of the bid.
3. As soon as the target is reached the bid closes automatically and the group order (all members as agents with their amounts, at the common price) is placed in the auction immediately.
4. The awarded group order is credited to every member's OWN account by its awarded share, i.e. the value of the bid is distributed by the power amount each member put into it. There is no joint account.
5. Bids which did not reach their target until the gate closure of their auction expire and are never submitted.

While the option is enabled, `POST /market/auction/grouporder` returns 403: a group order books payoff and penalties on every listed actor's account, so it may only originate from a cooperative bid which every member joined itself.

Endpoints (query parameters; every cooperative endpoint returns 403 while the option is disabled, except `/ui/cooperative`):

* `POST /market/cooperative/propose` with `actor_id`, `amount_kw`, `price_ct`, `supply_time` and optionally `target_amount_kw` -> `{"cooperative_bid": {...}}`. 404 for an unknown actor or without an open auction for the supply time, 400 for invalid (non-positive, NaN or infinite) amounts, prices or targets. The price is capped to the maximum price of the auction.
* `POST /market/cooperative/join` with `actor_id`, `cooperative_bid_id`, `amount_kw` -> `{"cooperative_bid": {...}, "accepted_amount_kw": ...}`. 404 for an unknown actor or bid, 409 if the bid is not open (or its auction closed meanwhile), 400 if the actor is already a member or the amount is not positive (or not finite).
* `GET /market/cooperative/open` with optional `supply_time` -> `{"cooperative_bids": [...]}`, only open bids sorted by supply time and creation time.
* `GET /market/cooperative/mine` with `actor_id` -> `{"cooperative_bids": [...]}`, bids of every status in which the actor is a member.
* `GET /ui/cooperative` -> `{"enabled": bool, "cooperative_bids": [...]}`, all bids of every status, newest first (at most 50); `{"enabled": false, "cooperative_bids": []}` while the option is disabled.

A cooperative bid is returned as `{"id", "auction_id", "supply_time", "product_type", "price_ct", "target_amount_kw", "filled_amount_kw", "remaining_amount_kw", "status" ("open" | "closed" | "expired"), "order_placed", "created_time", "members": [{"actor_id", "amount_kw"}]}`. The bids are part of the persisted state (`app_state.json`).
