from typing import List
from hackathon_backend.market.auction import AuctionResult
import pandas as pd

AMOUNT = "Amount in kW"
TOTAL_AMOUNT = "Total Order-amount in kW"
PRICE = "Price in ct per kW"
AGENTS = "Agents providing the kW"


class ElectricityAskAuctionAccounter:
    def __init__(self, auction_result: AuctionResult):
        self.result = auction_result
        if auction_result is not None:
            self.awarded_orders = self._generate_agent_dataframes()

    def _generate_agent_dataframes(self, ascending=True):
        agent_dataframes = {}
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
                                    TOTAL_AMOUNT: sum(awarded_order.awarded_amount_kw),
                                    AGENTS: set(agents),
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

    def return_awarded(self, agent):
        if self.result is None or agent not in self.awarded_orders:
            return []

        return self.awarded_orders[agent][AMOUNT]

    def return_awarded_agents(self, agent):
        if self.result is None or agent not in self.awarded_orders:
            return []

        return self.awarded_orders[agent][AGENTS]

    def calculate_payoff(self, agent, total_provided_amount):
        if self.result is None or agent not in self.awarded_orders:
            return 0

        total_awarded_amount = sum(self.awarded_orders[agent][AMOUNT])
        # TODO malus for missing energy

        if total_provided_amount >= total_awarded_amount:
            return (
                self.awarded_orders[agent][PRICE] * self.awarded_orders[agent][AMOUNT]
            ).sum()

        elif total_provided_amount <= 0:
            return 0
        else:
            amount_added_up = 0
            payoff = 0
            for _, row in self.awarded_orders[agent].iterrows():
                if amount_added_up + row[AMOUNT] <= total_provided_amount:
                    payoff += row[PRICE] * row[AMOUNT]
                    amount_added_up += row[AMOUNT]
                else:
                    remaining_amount = total_provided_amount - amount_added_up
                    payoff += row[PRICE] * remaining_amount
                    break

            return payoff
