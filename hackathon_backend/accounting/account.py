import json
from typing import Dict
from pydantic import BaseModel
import pandas as pd

AWARDED_AMOUNT = "Awarded amount in kW"
PROVIDED_POWER = "Provided power in kW"
PAYOFF = "Payoff in ct"
BALANCE = "Balance after transaction in ct"


class AccountData(BaseModel):
    transaction_payload: str


def _to_account(payload: str):
    account = Account()
    account.transactions = pd.DataFrame.from_dict(json.loads(payload))
    return account


def to_actor_accounts(account_data_dict: Dict[str, AccountData]):
    return {k: _to_account(v.transaction_payload) for k, v in account_data_dict.items()}


class Account:
    def __init__(self):
        self.transactions = pd.DataFrame(
            columns=[AWARDED_AMOUNT, PROVIDED_POWER, PAYOFF, BALANCE]
        )

    def add_transaction(self, awarded_amount, provided_power, payoff):
        if self.transactions.empty:
            balance = 0
        else:
            balance = self.transactions[BALANCE].iloc[-1]

        self.transactions = pd.concat(
            [
                self.transactions,
                pd.DataFrame(
                    [
                        {
                            AWARDED_AMOUNT: awarded_amount,
                            PROVIDED_POWER: provided_power,
                            PAYOFF: payoff,
                            BALANCE: balance + payoff,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    def get_balance(self):
        if self.transactions.empty:
            return 0
        return self.transactions[BALANCE].iloc[-1]

    def get_transactions(self):
        return self.transactions


def _to_account_data(account: Account):
    return AccountData(transaction_payload=account.transactions.to_json())


def to_actor_account_datas(account_dict: Dict[str, Account]):
    return {k: _to_account_data(v) for k, v in account_dict.items()}
