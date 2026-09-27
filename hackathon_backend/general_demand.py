import numpy as np
import pandas as pd
from hackathon_backend.profiles import DEMAND_PER_ACTOR_KW, hourly_to_steps
from hackathon_backend.units.load import *


N_TIME_INTERVALS = 96
# demand per registered actor, see hackathon_backend/profiles.py
DEFAULT_LOAD_PROFILE = np.array(hourly_to_steps(DEMAND_PER_ACTOR_KW, N_TIME_INTERVALS))


def demand_per_actor_profile(hourly_kw=DEMAND_PER_ACTOR_KW, steps=N_TIME_INTERVALS):
    """The market demand per registered actor for every step of the day in
    kW: the hourly values (config demand_per_actor_kw), linear in between,
    rounded to 1 W like the profile of the general demand unit."""
    return [float(value) for value in np.round(np.array(hourly_to_steps(hourly_kw, steps)), 3)]

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
        tender = self.supply[TENDER_AMOUNT].astype(float)
        if tender.sum() != 0:
            served = self.supply[PROVIDED_AMOUNT].astype(float).clip(upper=tender)
            self.supply.at[self.supply.index[-1], PROVIDED_SHARE] = served.sum() / tender.sum()


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
                    number_of_actors * DEFAULT_LOAD_PROFILE, 3
                ),
                perfect_demand_q_kvar=np.round(
                    number_of_actors * DEFAULT_LOAD_PROFILE, 3
                ),
                uncertainty=1,
            ),
        )
    else:
        return GeneralDemand(
            DemandInformation(
                unit_id=id,
                perfect_demand_p_kw=p_profile,
                perfect_demand_q_kvar=q_profile,
                uncertainty=1,
            )
        )
