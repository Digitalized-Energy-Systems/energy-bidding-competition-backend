from hackathon_backend.controller import Controller
import pandas as pd
from pathlib import Path


def to_participant(actor, mapping):
    new_actor = actor
    for aid, pid in mapping.items():
        new_actor = new_actor.replace(aid, pid[0:-2])
    return new_actor


class CsvScoreHandler:
    def __init__(self, time):
        self.time = time

    def write(self, controller: Controller):
        Path("results/").mkdir(parents=True, exist_ok=True)
        controller.general_demand.supply.to_csv(f"results/gm_{self.time}.csv")
        balance_dict = {
            to_participant(k, controller.actor_to_participant): v
            for k, v in controller.get_balance_dict_sync().items()
        }
        pd.DataFrame([balance_dict]).to_csv(f"results/agents_{self.time}.csv")
