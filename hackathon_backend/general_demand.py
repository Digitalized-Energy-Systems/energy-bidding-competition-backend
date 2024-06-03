import math
import numpy as np
import pandas as pd
from hackathon_backend.units.load import *


N_TIME_INTERVALS = 96
cos_values = np.array(
    [math.cos(2 * math.pi * x / N_TIME_INTERVALS) for x in range(N_TIME_INTERVALS)]
)
cos_amplitude = 0.1
DEFAULT_LOAD_PROFILE = 0.4 * np.ones(N_TIME_INTERVALS) - cos_amplitude * cos_values

TENDER_AMOUNT = "tender_amount_kw"
PROVIDED_AMOUNT = "provided_amount_kw"
PROVIDED_SHARE = "provided_share_until"


class GeneralDemand(SimpleDemandUnit):
    def __init__(self, demand_information: DemandInformation):
        super().__init__(demand_information)

        self.supply = pd.DataFrame(
            columns=[TENDER_AMOUNT, PROVIDED_AMOUNT, PROVIDED_SHARE]
        )

    def step(self, input, step):
        return super().step(input, step)

    def notify_supply(self, tender_amount_kw, provided_amount_kw):
        # add tender and provided amount
        self.supply = pd.concat(
            [
                self.supply,
                pd.DataFrame(
                    [
                        {
                            TENDER_AMOUNT: tender_amount_kw,
                            PROVIDED_AMOUNT: provided_amount_kw,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        # calculate provided share
        if self.supply[TENDER_AMOUNT].sum() != 0:
            self.supply.at[self.supply.index[-1], PROVIDED_SHARE] = (
                self.supply[PROVIDED_AMOUNT].sum() / self.supply[TENDER_AMOUNT].sum()
            )


def create_general_demand(
    id,
    p_profile: List = None,
    q_profile: List = None,
    number_of_actors=1,
):
    if p_profile is None:
        return GeneralDemand(
            DemandInformation(
                unit_id=id,
                perfect_demand_p_kw=np.round(
                    number_of_actors * DEFAULT_LOAD_PROFILE, 1
                ),
                perfect_demand_q_kvar=np.round(
                    number_of_actors * DEFAULT_LOAD_PROFILE, 1
                ),
                uncertainty=1,
            ),
        )
    else:
        return GeneralDemand(id, SimpleDemand(p_profile, q_profile, 1))
