import json
from pydantic import BaseModel, Field
from typing import List, Optional


DEFAULT_CONFIG_FILE = "config.json"

class Config(BaseModel):
    participants: List[str]
    rt_step_duration_s: float
    rt_step_init_delay_s: float
    pause: bool
    max_steps: int
    test_mode: bool
    # cooperative bidding: the minimum order amount follows a profile which
    # cannot be reached by a single actor for about half of the day (see
    # hackathon_backend/market/tender.py); actors can pool their power in a
    # cooperative bid (see hackathon_backend/market/cooperative.py). The
    # tender scales with the registered actors like in the plain mode.
    cooperative_bidding: bool = False
    # the minimum order amount is at most this share of the tender: the
    # tender is floored at minimum / share (0 < share <= 1). With the default
    # of 1 the tender is at least the minimum, so one minimum-sized bid
    # always fits, which keeps the market contested from 3 actors on; 0.5
    # (two bids fit) keeps the night tight for 8 or more actors but leaves
    # fields of up to 4 actors unable to fill the tender at night (see
    # hackathon_backend/market/tender.py)
    cooperative_min_order_share: float = Field(default=1.0, gt=0, le=1)
    # token for the admin endpoints (/admin/*); they are disabled while no
    # token is configured
    admin_token: Optional[str] = None


def load_config(config_file) -> Config:
    with open(config_file) as f:
        return Config.model_validate_json(f.read())


def load_default_config() -> Config:
    return load_config(DEFAULT_CONFIG_FILE)
