from hackathon_backend.controller import Controller
import pandas as pd
from pathlib import Path


class CsvScoreHandler:
    def __init__(self, time):
        self.time = time

    def write(self, controller: Controller):
        Path("results/").mkdir(parents=True, exist_ok=True)
        controller.general_demand.supply.to_csv(f"results/gm_{self.time}.csv")
        names = controller.participant_display_names()
        balance_dict = {
            names.get(k, k): v for k, v in controller.get_balance_dict_sync().items()
        }
        pd.DataFrame([balance_dict]).to_csv(f"results/agents_{self.time}.csv")
