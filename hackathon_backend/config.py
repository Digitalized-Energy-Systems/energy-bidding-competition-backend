import json
from pydantic.dataclasses import dataclass
from pydantic import BaseModel
from typing import List


DEFAULT_CONFIG_FILE = "config.json"


@dataclass
class Config(BaseModel):
    participants: List[str]
    rt_step_duration_s: float
    rt_step_init_delay_s: float
    pause: bool
    max_steps: int
    test_mode: bool


def load_config(config_file) -> Config:
    with open(config_file) as f:
        return Config.model_validate_json(f.read())


def load_default_config() -> Config:
    return load_config(DEFAULT_CONFIG_FILE)
