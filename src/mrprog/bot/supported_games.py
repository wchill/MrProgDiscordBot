from typing import Literal, Dict

from mmbn.gamedata.chip_list import ChipList
from mmbn.gamedata.ncp_list import NcpList

SUPPORTED_GAMES = {"switch": [3, 4, 5, 6], "steam": [3, 4, 5, 6]}
SupportedGameLiteral = Literal[3, 4, 5, 6]
SupportedPlatformLiteral = Literal["Switch", "Steam"]


CHIP_LISTS: Dict[int, ChipList] = {
    3: ChipList(3),
    4: ChipList(4),
    5: ChipList(5),
    6: ChipList(6)
}


NCP_LISTS: Dict[int, NcpList] = {
    3: NcpList(3),
    4: NcpList(4),
    5: NcpList(5),
    6: NcpList(6)
}
