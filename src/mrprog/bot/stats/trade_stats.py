import collections
import pickle
from typing import Dict, List, Tuple

from mrprog.utils.types import TradeItem


class UserTradeStats:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.trades: Dict[TradeItem, int] = collections.defaultdict(int)

    def add_trade(self, trade: TradeItem):
        self.trades[trade] += 1

    def get_total_trade_count(self) -> int:
        total = 0
        for qty in self.trades.values():
            total += qty
        return total

    def get_trades_by_trade_count(self) -> List[Tuple[TradeItem, int]]:
        return [(k, v) for k, v in sorted(self.trades.items(), key=lambda item: item[1], reverse=True)]


class BotTradeStats:
    def __init__(self):
        self.users: Dict[int, UserTradeStats] = {}

    def add_trade(self, user_id: int, trade_item: TradeItem):
        if user_id not in self.users:
            self.users[user_id] = UserTradeStats(user_id)
        self.users[user_id].add_trade(trade_item)

    def get_total_trade_count(self) -> int:
        total = 0
        for user in self.users.values():
            total += user.get_total_trade_count()
        return total

    def get_total_user_count(self) -> int:
        return len(self.users)

    def get_users_by_trade_count(self) -> List[UserTradeStats]:
        all_users = self.users.values()
        sorted_users = sorted(all_users, key=lambda user: user.get_total_trade_count(), reverse=True)
        return sorted_users

    def get_trades_by_trade_count(self) -> List[Tuple[TradeItem, int]]:
        all_items: Dict[TradeItem, int] = collections.defaultdict(int)
        for user in self.users.values():
            for trade_item in user.trades:
                all_items[trade_item] += user.trades[trade_item]

        item_tuples = [(item, all_items[item]) for item in all_items]
        return sorted(item_tuples, key=lambda chip_tuple: chip_tuple[1], reverse=True)

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load_or_default(cls, path: str) -> "BotTradeStats":
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except FileNotFoundError:
            return cls()
