from pysimmods.model.inputs import ModelInputs


class MarketInputs(ModelInputs):
    """
    Inherited class from Pysimmods Inputs for market
    """


class Market:
    """
    Needed functionality:
    - receive auctions
    - receive orders by type and time and map them to auctions
    - return list of open auctions by type and time
    - return list of auction results by type and time
    - step auctions
    Needed checks:
    - check if order fits to auction, otherwise return error
    """

    def __init__(self):
        self.inputs: MarketInputs = MarketInputs()
        self.create_empty_attributes()

    def create_empty_attributes(self):
        self.auctions = {}
        self.open_auctions = []
        self.current_auction_results = []
        self.expired_auctions = {}

    def step(self):
        current_time = self.inputs.now_dt.timestamp()

        # Update auction data
        self.open_auctions = []
        self.current_auction_results = []
        # copy to avoid changing dict during iteration
        auctions = self.auctions.copy()
        for auction_id, auction in auctions.items():
            # step auctions
            auction.step(current_time)
            if auction.status == "open":
                self.open_auctions.append(auction)
            elif auction.status == "closed":
                self.current_auction_results.append(auction.result)
            elif auction.status == "expired":
                self.expired_auctions[auction_id] = auction
                del self.auctions[auction_id]

    def receive_auction(self, new_auction):
        """
        Receives auctions
        """
        self.auctions[new_auction.id] = new_auction

    # method to return open auctions as a list of dicts
    def get_open_auctions(self):
        """
        Returns open auctions as a list of dicts
        """
        return [auction.to_dict() for auction in self.open_auctions]

    # method to return current auction results
    def get_current_auction_results(self):
        """
        Returns current auction results
        """
        return {
            f"{int(result.params.supply_start_time)}_{result.params.product_type}": result
            for result in self.current_auction_results
        }

    # method to receive orders and map them to auctions
    def receive_order(
        self, amount_kw, price_ct, agents, supply_time, product_type, auction_id=None
    ):
        """
        Receives orders and maps them to auctions
        """
        if auction_id is None:
            auction_id = self._get_auction_id_from_supply_time_and_product_type(
                supply_time, product_type
            )
        if auction_id is not None:
            self.auctions[auction_id].place_order(
                amount_kw=amount_kw, price_ct=price_ct, agents=agents
            )
            return True
        else:
            return False

    def _get_supply_time_and_product_type_from_auction_id(self, auction_id):
        """
        Translates auction_id to supply time and product type
        """
        if auction_id in self.auctions:
            return (
                self.auctions[auction_id].params.supply_start_time,
                self.auctions[auction_id].params.product_type,
            )
        else:
            return None, None

    # method to translate the supply time and product type of auction to key
    # within self.auctions
    def _get_auction_id_from_supply_time_and_product_type(
        self, supply_time, product_type
    ):
        """
        Translates the supply time and product type of auction
        to key within self.auctions
        """
        for auction_id, auction in self.auctions.items():
            if (
                auction.params.supply_start_time == supply_time
                and auction.params.product_type == product_type
            ):
                return auction_id
        return None

    def reset(self):
        self.create_empty_attributes()
