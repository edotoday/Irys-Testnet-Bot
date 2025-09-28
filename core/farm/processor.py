import asyncio
import multiprocessing
import os
import platform
import random
import sys
import psutil

from typing import List, Any, Dict
from loguru import logger

from loader import config, proxy_manager
from models import Account
from database import Accounts, initialize_database
from utils import setup_logs, validate_error, WebSocketStats

from core.farm.websocket_client import WebSocketClient
from core.modules.auth import ClientAuth
from core.exceptions.tracker import TooManyErrorsException


class FarmProcessor:
    def __init__(self, accounts: List[Account], ws_counter: Any):
        self.accounts = accounts
        self.ws_counter = ws_counter

    @staticmethod
    def distribute_proxies(process_count: int) -> Dict[int, List[str]]:
        distributed_proxies = {}
        total_proxies = len(config.proxies)

        proxies_per_process = total_proxies // process_count
        remainder = total_proxies % process_count

        start_idx = 0
        for i in range(process_count):
            current_count = proxies_per_process + (1 if i < remainder else 0)
            end_idx = start_idx + current_count

            distributed_proxies[i] = config.proxies[start_idx:end_idx]
            start_idx = end_idx

        return distributed_proxies


    @staticmethod
    async def process_delay(account: Accounts, process_id: int) -> None:
        if config.attempts_and_delay_settings.delay_before_start.max > 0:
            delay = random.randint(
                config.attempts_and_delay_settings.delay_before_start.min + 1,
                config.attempts_and_delay_settings.delay_before_start.max
            )
            logger.info(f"Process: {process_id} | Account: {account.wallet_address} | Initial delay set to {delay} seconds | Execution will start in {delay} seconds")
            await asyncio.sleep(delay)

    async def handle_websocket(self, account: Accounts, process_id: int, ws_stats: WebSocketStats) -> str | None:
        await self.process_delay(account, process_id)

        while True:
            error: Exception | None = None
            account = await Accounts.get_account(wallet_address=account.wallet_address)
            if not account:
                logger.error(f"Process: {process_id} | Account {account.wallet_address} was deleted from the database.")
                return None

            if not account.active_account_proxy:
                proxy = await proxy_manager.get_proxy()
                await account.update_account(active_account_proxy=proxy)

            try:
                client = WebSocketClient(account, process_id, ws_stats)
                await client.connect()

            except TooManyErrorsException as e:
                error = e
                logger.error(f"Process: {process_id} | Account {account.wallet_address} | {error}")

            except Exception as e:
                error = e
                logger.error(f"Process: {process_id} | Account {account.wallet_address} | WebSocket error: {error}")

            if config.application_settings.disable_auto_proxy_change is False:
                await proxy_manager.release_proxy(account.active_account_proxy)
                proxy = await proxy_manager.get_proxy()
                await account.update_account(active_account_proxy=proxy)
                logger.info(f"Process: {process_id} | Account {account.wallet_address} | Proxy changed | Reconnecting...")

            else:
                if error is not None and "Proxy Authentication Required" in str(error):
                    logger.error(f"Process: {process_id} | Account {account.wallet_address} | Proxy authentication failed | Account deleted from farming list while auto proxy change is disabled.")
                    return "invalid proxy"

                logger.info(f"Process: {process_id} | Account {account.wallet_address} | Reconnecting...")

            await asyncio.sleep(5)

    @staticmethod
    async def _prepare_accounts(accounts: List[Account]) -> List[Accounts]:
        tasks = [
            asyncio.create_task(Accounts.get_account(wallet_address=account.wallet_address))
            for account in accounts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        prepared_accounts = []

        for result in results:
            if isinstance(result, Accounts):
                prepared_accounts.append(result)

        return prepared_accounts

    @staticmethod
    async def _verify_auth(process_id: int, client_auth: ClientAuth = None) -> None | str:
        if process_id == 0 and client_auth:
            if not client_auth.refresh_task:
                logger.info(f"Process: {process_id} | Starting token refresh loop")
                client_auth.refresh_task = asyncio.create_task(client_auth.token_refresh_loop())

            elif client_auth.refresh_task.done():
                try:
                    result = client_auth.refresh_task.result()
                    logger.error(f"Process: {process_id} | Token refresh loop finished with result: {result}")
                    return "terminate"
                except Exception as error:
                    logger.error(f"Process: {process_id} | Token refresh finished with error: {error}")
                    return "terminate"

    async def farm_continuously(self, accounts: List[Account], process_id: int, client_auth: ClientAuth = None, ws_stats: Any = None) -> None | str:
        try:
            prepared_accounts = await self._prepare_accounts(accounts)
            logger.success(f"Process: {process_id} | Prepared {len(prepared_accounts)} accounts for farming.")
            task_params = []
            tasks = []

            for account in prepared_accounts:
                if account is None:
                    continue

                task_params.append(account)
                tasks.append(asyncio.create_task(
                    self.handle_websocket(account, process_id, ws_stats)
                ))

            try:
                while True:
                    result = await self._verify_auth(process_id, client_auth)
                    if result == "terminate":
                        return "terminate"

                    await asyncio.sleep(60)
                    active_tasks = len([t for t in tasks if not t.done()])

                    if active_tasks < len(tasks):
                        dead_indices = [i for i, t in enumerate(tasks) if t.done()]
                        dead_indices_reasons = []

                        for i in dead_indices:
                            task = tasks[i]
                            try:
                                result = task.result()
                            except Exception as e:
                                result = str(e)
                            dead_indices_reasons.append(result)

                        logger.warning(f"Process: {process_id} | {len(dead_indices)} connections died")
                        for idx, reason in zip(dead_indices, dead_indices_reasons):
                            if isinstance(reason, str) and reason == "invalid proxy":
                                continue

                            logger.info(f"Process: {process_id} | Restarting connection with index {idx}..")
                            account_id = task_params[idx]
                            tasks[idx] = asyncio.create_task(
                                self.handle_websocket(account_id, process_id, ws_stats)
                            )

                    self._log_system_stats(process_id, len(tasks), ws_stats)

            except asyncio.CancelledError as error:
                logger.info(f"Farming task was cancelled | Process: {process_id}")
            except Exception as e:
                logger.error(f"Farming error: {e} | Process: {process_id}")
            finally:
                for t in tasks:
                    if not t.done() and not t.cancelled():
                        t.cancel()

                await asyncio.gather(*tasks, return_exceptions=True)
                tasks.clear()

        except Exception as error:
            logger.error(f"Process: {process_id} | Error while farming continuously: {error}")

    @staticmethod
    def _log_system_stats(process_id: int, total_tasks: int, ws_stats: WebSocketStats) -> None:
        process = psutil.Process(os.getpid())

        try:
            ws_count = ws_stats.count()

        except Exception as e:
            logger.warning(f"Process: {process_id} | Cannot determine active TCP connections in system: {e}")
            ws_count = "N/A"

        logger.info(
            f"\n========== [SYSTEM STATS] ==========\n"
            f"Process: {process_id}\n"
            f"Active TCP connections (global): {ws_count}\n"
            f"Memory usage by process: {process.memory_info().rss / 1024 / 1024:.2f} MB\n"
            f"Running accounts by process: {total_tasks}\n"
            f"====================================\n"
        )

    @staticmethod
    async def graceful_shutdown():
        pending = [t for t in asyncio.all_tasks() if not t.done() and not t.cancelled()]
        if pending:
            for t in pending:
                t.cancel()

            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    def run_farm_process(self, accounts_subset: List[Account], process_id: int, process_proxies: List[str], client_auth: ClientAuth = None, ws_counter: Any = None):
        if platform.system() == 'Windows':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        async def init_and_farm():
            setup_logs()
            await initialize_database()
            proxy_manager.load_proxy(process_proxies)
            ws_stats = WebSocketStats(ws_counter)

            if config.attempts_and_delay_settings.delay_before_start.min < 30:
                logger.warning(f"Min Delay before start is less than 30 seconds. Setting delay to 30 seconds.")
                config.attempts_and_delay_settings.delay_before_start.min = 30

            if config.attempts_and_delay_settings.delay_before_start.max < 60:
                logger.warning(f"Max Delay before start is less than 60 seconds. Setting delay to 60 seconds.")
                config.attempts_and_delay_settings.delay_before_start.max = 60

            try:
                result = await self.farm_continuously(accounts_subset, process_id, client_auth, ws_stats)
                if result == "terminate":
                    logger.error(f"Process: {process_id} terminating due to token failure")
                    sys.exit(1)
            except Exception as error:
                logger.error(f"Process: {process_id} | Error during farming: {error} | Deactivating farming process.")
            finally:
                await self.graceful_shutdown()

        try:
            asyncio.run(init_and_farm())
        except SystemExit as e:
            logger.info(f"Process: {process_id} | Exiting with code {e.code}")

    @staticmethod
    async def get_processes_count() -> int:
        if config.application_settings.cpu_thread_count == 0:
            process_count = int(multiprocessing.cpu_count())
            process_count = process_count - 1 if process_count > 1 else 1
        else:
            process_count = config.application_settings.cpu_thread_count

        return process_count

    async def run_multiprocess_farm(self, client_auth: ClientAuth) -> None:
        try:
            process_count = await self.get_processes_count()
            logger.info(f"Starting farming with {process_count} CPU processes")

            distributed_proxies = self.distribute_proxies(process_count)
            accounts_per_process = len(self.accounts) // process_count
            processes = []

            for i in range(process_count):
                start_idx = i * accounts_per_process
                end_idx = start_idx + accounts_per_process if i < process_count - 1 else len(self.accounts)
                accounts_subset = self.accounts[start_idx:end_idx]

                process_proxies = distributed_proxies[i]
                if len(process_proxies) == 0:
                    logger.critical("No available proxies, please add more proxies to the file and restart the application.")
                    sys.exit(1)

                logger.info(f"Process: {i} | Starting with {len(accounts_subset)} accounts and {len(process_proxies)} proxies")

                p = multiprocessing.Process(
                    target=self.run_farm_process,
                    args=(accounts_subset, i, process_proxies, client_auth, self.ws_counter)
                )
                processes.append(p)
                p.start()

            while True:
                if not processes[0].is_alive():
                    logger.error("Main (zero) process has exited. Shutting down all processes.")

                    for proc in processes:
                        if proc.is_alive():
                            proc.terminate()

                    sys.exit(1)

                await asyncio.sleep(1)

        except Exception as error:
            logger.error(f"Error while running multiprocess farm: {error}")
