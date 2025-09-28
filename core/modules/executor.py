from core.bot.base import Bot
from loader import file_operations
from models import Account


class ModuleExecutor:
    def __init__(self, account: Account = None):
        self.account = account
        self.bot = Bot(account)

    async def _process_request_tokens_from_faucet(self) -> None:
        operation_result = await self.bot.process_request_tokens_from_faucet()
        if isinstance(operation_result, dict):
            await file_operations.export_result(operation_result, "request_tokens")


    async def _process_top_up_game_balance(self) -> None:
        operation_result = await self.bot.process_top_up_game_balance()
        if isinstance(operation_result, dict):
            await file_operations.export_result(operation_result, "top_up_game_balance")


    async def _process_play_games(self) -> None:
        operation_result = await self.bot.process_play_games()
        if isinstance(operation_result, dict):
            await file_operations.export_result(operation_result, "play_games")


    async def _process_mint_omnihub_nft(self) -> None:
        operation_result = await self.bot.process_mint_omnihub_nft()
        if isinstance(operation_result, dict):
            await file_operations.export_result(operation_result, "mint_nft")


    async def _process_all_in_one(self) -> None:
        operation_result = await self.bot.process_all_in_one()
        if isinstance(operation_result, dict):
            await file_operations.export_result(operation_result, "all_in_one")
