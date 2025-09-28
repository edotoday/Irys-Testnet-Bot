import asyncio
import random

from typing import List, Any, Set, Optional, Callable
from loguru import logger

from core.modules.executor import ModuleExecutor
from loader import config, file_operations, semaphore, proxy_manager
from models import Account
from utils import Progress
from console import Console
from database import initialize_database, Accounts


class ApplicationManager:
    def __init__(self):
        self.accounts_with_initial_delay: Set[str] = set()
        self.module_map = {
            "request_tokens_from_faucet": (config.accounts_to_request_tokens, self._execute_module_for_accounts),
            "top_up_game_balance": (config.accounts_to_top_up_game_balance, self._execute_module_for_accounts),
            "play_games": (config.accounts_to_play_games, self._execute_module_for_accounts),
            "mint_omnihub_nft": (config.accounts_to_mint_nft, self._execute_module_for_accounts),
            "all_in_one": (config.accounts_for_all_in_one, self._execute_module_for_accounts),
        }

    @staticmethod
    async def initialize() -> None:
        logger.info(f"Initializing database..")
        await initialize_database()
        logger.success(f"Database initialized")
        await file_operations.setup_files()

    async def _execute_module_for_accounts(
        self, accounts: List[Account], module_name: str
    ) -> list[Any]:
        progress = Progress(len(accounts))

        if module_name == "export_stats":
            await file_operations.setup_stats()

        tasks = []
        for account in accounts:
            executor = ModuleExecutor(account)
            module_func = getattr(executor, f"_process_{module_name}")
            tasks.append(self._safe_execute_module(account, module_func, progress))

        return await asyncio.gather(*tasks)

    async def _safe_execute_module(
            self, account: Account, module_func: Callable, progress: Progress
    ) -> Optional[dict]:
        try:
            async with semaphore:
                if (
                    config.attempts_and_delay_settings.delay_before_start.min > 0
                    and config.attempts_and_delay_settings.delay_before_start.max > 0
                ):
                    if account.wallet_address not in self.accounts_with_initial_delay:
                        random_delay = random.randint(
                            config.attempts_and_delay_settings.delay_before_start.min,
                            config.attempts_and_delay_settings.delay_before_start.max
                        )
                        logger.info(
                            f"Account: {account.wallet_address} | Initial delay set to {random_delay} seconds | Execution will start in {random_delay} seconds"
                        )
                        self.accounts_with_initial_delay.add(account.wallet_address)
                        await asyncio.sleep(random_delay)

                result = await module_func()
                if module_func.__name__ != "_process_farm":
                    progress.increment()
                    logger.debug(f"Progress: {progress.processed}/{progress.total}")

                return result

        except Exception as e:
            logger.error(f"Error processing account {account.wallet_address}: {str(e)}")
            return {"success": False, "error": str(e)}


    @staticmethod
    async def _clean_accounts_proxies() -> None:
        logger.info("Cleaning all accounts proxies..")
        try:
            cleared_count = await Accounts().clear_all_accounts_proxies()
            logger.success(f"Successfully cleared proxies for {cleared_count} accounts")

        except Exception as e:
            logger.error(f"Error while clearing accounts proxies: {str(e)}")

    async def run(self) -> None:
        while True:
            await Console().build()

            if config.module == "clean_accounts_proxies":
                await self._clean_accounts_proxies()
                input("\nPress Enter to continue...")
                continue

            if config.module not in self.module_map:
                logger.error(f"Unknown module: {config.module}")
                break

            proxy_manager.load_proxy(config.proxies)
            accounts, process_func = self.module_map[config.module]

            if config.application_settings.shuffle_accounts:
                random.shuffle(accounts)

            if not accounts:
                logger.error(f"No accounts for {config.module}")
                input("\nPress Enter to continue...")
                continue

            await self._execute_module_for_accounts(accounts, config.module)
            input("\nPress Enter to continue...")
