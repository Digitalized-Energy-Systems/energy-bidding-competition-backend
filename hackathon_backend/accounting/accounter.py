from hackathon_backend.market.auction import AuctionResult
import pandas as pd

class ElectricityAskAuctionAccounter:
    def __init__(self, auction_result: AuctionResult):
        self.result = auction_result
        if auction_result is not None:
            self.awarded_orders = self._generate_agent_dataframes()

    def _generate_agent_dataframes(self, ascending=True):
        agent_dataframes = {}
        for awarded_order in self.result.awarded_orders:
            agent = awarded_order.agent
            if agent not in agent_dataframes:
                agent_dataframes[agent] = pd.DataFrame(
                    columns=["Amount in kW", "Price in ct per kW"])

            agent_dataframes[agent] = pd.concat([
                agent_dataframes[agent], 
                pd.DataFrame([{
                    "Amount in kW": awarded_order.awarded_amount_kw,
                    "Price in ct per kW": awarded_order.price_ct
                }])
            ], ignore_index=True)

        for agent in agent_dataframes:
            agent_dataframes[agent] = agent_dataframes[agent].sort_values(
                by="Price in ct per kW", ascending=ascending).reset_index(drop=True)
        return agent_dataframes
    
    def return_awarded_sum(self, agent):
        if self.result is None or agent not in self.awarded_orders:
            return 0
        
        return self.awarded_orders[agent]["Amount in kW"].sum()
    
    def calculate_payoff(self, agent, total_provided_amount):
        if self.result is None or agent not in self.awarded_orders:
            return 0
        
        total_awarded_amount = sum(self.awarded_orders[agent]["Amount in kW"])

        if total_provided_amount >= total_awarded_amount:
            return (
                self.awarded_orders[agent]["Price in ct per kW"] *
                self.awarded_orders[agent]["Amount in kW"]
            ).sum()
        
        elif total_provided_amount <= 0:
            return 0
        else:
            amount_added_up = 0
            payoff = 0
            for index, row in self.awarded_orders[agent].iterrows():
                if amount_added_up + row["Amount in kW"] <= total_provided_amount:
                    payoff += row["Price in ct per kW"] * row["Amount in kW"]
                    amount_added_up += row["Amount in kW"]
                else:
                    remaining_amount = total_provided_amount - amount_added_up
                    payoff += row["Price in ct per kW"] * remaining_amount
                    break

            return payoff