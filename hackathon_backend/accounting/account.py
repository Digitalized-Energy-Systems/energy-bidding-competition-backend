import pandas as pd

AWARDED_AMOUNT = "Awarded amount in kW"
PROVIDED_POWER = "Provided power in kW"
PAYOFF = "Payoff in ct"
BALANCE = "Balance after transaction in ct"

class Account:
    def __init__(self):
        self.transactions = pd.DataFrame(columns=[AWARDED_AMOUNT, PROVIDED_POWER, PAYOFF, BALANCE])

    def add_transaction(self, awarded_amount, provided_power, payoff):
        balance = self.transactions[BALANCE].iloc[-1] + payoff
        self.transactions = pd.concat([
            self.transactions,
            pd.DataFrame([{
                AWARDED_AMOUNT: awarded_amount,
                PROVIDED_POWER: provided_power,
                PAYOFF: payoff,
                BALANCE: balance
            }])
        ], ignore_index=True)

    def get_balance(self):
        return self.transactions[BALANCE].iloc[-1]

    def get_transactions(self):
        return self.transactions