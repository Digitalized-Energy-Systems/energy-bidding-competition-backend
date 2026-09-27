from typing import List
from hackathon_backend.market.auction import AuctionResult
import pandas as pd

AMOUNT = "Amount in kW"
PRICE = "Price in ct per kW"

# Smallest shortfall rate in ct per kW and quarter hour (a quarter of the
# price cap of 1000 ct; config.shortfall_penalty_ct_per_kw): awarded but not
# provided power is charged at the order price but at least at this rate, so that not delivering is never
# free: an order at 0 ct which blocks the tender still pays for its
# shortfall. Grid draw is charged separately at
# config.grid_penalty_ct_per_kw (see Controller.step_units).
PENALTY_CT_PER_KW = 1000 / 4


class ElectricityAskAuctionAccounter:
    def __init__(self, auction_result: AuctionResult, shortfall_penalty_ct_per_kw=PENALTY_CT_PER_KW):
        self.result = auction_result
        self.shortfall_penalty_ct_per_kw = shortfall_penalty_ct_per_kw
        if auction_result is not None:
            self.awarded_orders = self._generate_agent_dataframes()

    def _generate_agent_dataframes(self, ascending=True):
        agent_dataframes = {}
        # one dataframe per agent with the agent's OWN awarded share of every
        # order (a group order yields one row per member); there is no
        # joint account
        for awarded_order in self.result.awarded_orders:
            agents = awarded_order.agents
            for i, agent in enumerate(agents):
                if agent not in agent_dataframes:
                    agent_dataframes[agent] = pd.DataFrame(columns=[AMOUNT, PRICE])

                agent_dataframes[agent] = pd.concat(
                    [
                        agent_dataframes[agent],
                        pd.DataFrame(
                            [
                                {
                                    AMOUNT: awarded_order.awarded_amount_kw[i],
                                    PRICE: awarded_order.price_ct,
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )

        for agent in agent_dataframes:
            agent_dataframes[agent] = (
                agent_dataframes[agent]
                .sort_values(by=PRICE, ascending=ascending)
                .reset_index(drop=True)
            )
        return agent_dataframes

    def return_awarded_sum(self, agent):
        if self.result is None or agent not in self.awarded_orders:
            return 0

        return self.awarded_orders[agent][AMOUNT].sum()

    def calculate_payoff(self, agent, total_provided_amount):
        if self.result is None or agent not in self.awarded_orders:
            return 0
        
        # the provided power fills the actor's orders from the cheapest one
        # on; the shortfall of every order is charged at its price, but at
        # least at the penalty rate
        awarded_amount_added_up = 0
        payoff = 0
        for _, row in self.awarded_orders[agent].iterrows():
            order_provided_amount = max(
                min(row[AMOUNT], total_provided_amount - awarded_amount_added_up), 0
            )
            shortfall = row[AMOUNT] - order_provided_amount
            payoff += row[PRICE] * order_provided_amount
            payoff -= max(row[PRICE], self.shortfall_penalty_ct_per_kw) * shortfall

            awarded_amount_added_up = row[AMOUNT] + awarded_amount_added_up

        return payoff
