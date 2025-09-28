from typing import Literal, TypedDict


ModuleType = Literal["request_tokens", "stats", "top_up_game_balance", "play_games", "mint_nft", "all_in_one"]
GameType = Literal["snake", "asteroids", "hex-shooter", "missile-command", "spritetype"]


class OperationResult(TypedDict):
    pk_or_mnemonic: str
    data: str | dict | None
    status: bool
