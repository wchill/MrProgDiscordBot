import collections
import logging
import pickle
from typing import Dict, List, Tuple

from mmbn.gamedata.chip_list import ChipList
from mrprog.utils.types import TradeItem

logger = logging.getLogger(__name__)


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
        logger.info(f"Loading bot stats from {path}")
        # TODO: Remove this later
        try:
            from mmbn.gamedata.chip import Chip
            chip_lists = {
                game: ChipList(game) for game in [1, 2, 3, 4, 5, 6]
            }
            with open(path, "rb") as f:
                stats = pickle.load(f)
            for _, uts in stats.items():
                new_trades = {}
                for item, count in uts.trades.items():
                    if isinstance(item, Chip):
                        game = int(item.__class__.__name__.replace("BN", "").replace("Chip", ""))
                        chip = chip_lists[game].get_chip(item.name, item.code)

                        if chip is None:
                            continue

                        if item.chip_type == 2 and chip.chip_type == Chip.MEGA:
                            item.chip_type = Chip.MEGA
                        elif item.chip_type == 3 and chip.chip_type == Chip.GIGA:
                            item.chip_type = Chip.GIGA
                    new_trades[item] = count
                uts.trades = new_trades
            with open(path, "wb") as f:
                pickle.dump(stats, f)
            return stats
        except FileNotFoundError:
            logger.debug("Bot stats don't exist, creating a new one")
            return cls()
