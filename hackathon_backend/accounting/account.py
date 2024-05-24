import pandas as pd

class Account:
    def __init__(self):
        self.balance = 0
        self.transactions = pd.DataFrame(columns=["Awarded amount in kW", "Provided power in kW", "Payoff in ct", "Balance after transaction in ct"])

    def add_transaction(self, awarded_amount, provided_power, payoff):
        self.balance += payoff
        self.transactions = pd.concat([
            self.transactions,
            pd.DataFrame([{
                "Awarded amount in kW": awarded_amount,
                "Provided power in kW": provided_power,
                "Payoff in ct": payoff,
                "Balance after transaction in ct": self.balance
            }])
        ], ignore_index=True)

    def get_balance(self):
        return self.balance

    def get_transactions(self):
        return self.transactions