import asyncio
import random
import time

from typing import Literal, Optional

from loguru import logger
from better_proxy import Proxy

from core.onchain.wallet import Web3Wallet
from loader import config, file_operations, proxy_manager, captcha_solver
from models import Account, OperationResult, GameType
from database import Accounts
from core.api.irys import IrysAPI
from core.exceptions.base import (
    APIError,
    CaptchaSolvingFailed, APIErrorType
)
from utils import (
    operation_failed, operation_success,
    validate_error, generate_session_id,
    generate_anti_cheat_hash, generate_sprite_game_stats
)
from core.onchain import IrysGamesModule, IrysOmnihubModule


class Bot:
    def __init__(self, account_data: Account = None):
        self.account_data = account_data
        self._db_account: Optional[Accounts] = None

    async def _ensure_db_account(self) -> Accounts:
        if self._db_account is not None:
            return self._db_account

        wallet = self.account_data.wallet_address
        db_account = await Accounts.get_account(wallet_address=wallet)

        if db_account is None:
            proxy = await self._prepare_proxy()

            db_account = await Accounts.create_account(
                wallet_address=wallet,
                private_key=self.account_data.private_key,
                active_account_proxy=proxy,
            )

        if not db_account.active_account_proxy:
            proxy = await self._prepare_proxy()
            await db_account.update_account(active_account_proxy=proxy)

        self._db_account = db_account
        return db_account

    @staticmethod
    async def _prepare_proxy() -> str:
        proxy = await proxy_manager.get_proxy()
        return proxy.as_url if isinstance(proxy, Proxy) else proxy

    async def _update_account_proxy(self, attempt: int, max_attempts: int) -> None:
        error_delay = config.attempts_and_delay_settings.error_delay

        if config.application_settings.disable_auto_proxy_change is False:
            if self._db_account and self._db_account.active_account_proxy:
                await proxy_manager.release_proxy(self._db_account.active_account_proxy)

            if not self._db_account:
                logger.info(
                    f"Account: {self.account_data.wallet_address} | Proxy changed | "
                    f"Retrying in {error_delay}s.. | Attempt: {min(attempt + 1, max_attempts)}/{max_attempts}.."
                )
                await asyncio.sleep(error_delay)
                return

            proxy = await self._prepare_proxy()
            await self._db_account.update_account_proxy(proxy)
            msg = "Proxy changed"
        else:
            msg = "Proxy change disabled"

        logger.info(
            f"Account: {self.account_data.wallet_address} | {msg} | "
            f"Retrying in {error_delay}s.. | Attempt: {min(attempt + 1, max_attempts)}/{max_attempts}.."
        )
        await asyncio.sleep(error_delay)

    @staticmethod
    def tx_hash_to_explorer_link(tx_hash: str) -> str:
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash

        return f"https://testnet-explorer.irys.xyz/tx/{tx_hash}"

    @staticmethod
    async def get_captcha(wallet_address: str, captcha_type: Literal["cf", "geetest"], action: str = None) -> Optional[str]:
        max_attempts = config.attempts_and_delay_settings.max_captcha_attempts

        async def handle_turnistale() -> Optional[str]:
            logger.info(f"Account: {wallet_address} | Solving Cloudflare captcha | Attempt: {attempt + 1}/{max_attempts}")

            success, result = await captcha_solver.solve_turnistale(
                site_key="0x4AAAAAAA6vnrvBCtS4FAl-",
                page_url="https://irys.xyz/faucet"
            )

            if success:
                logger.success(f"Account: {wallet_address} | Cloudflare captcha solved")
                return result

            raise ValueError(f"{result}")

        async def handle_geetest() -> Optional[str]:
            logger.info(f"Account: {wallet_address} | Solving Geetest captcha | Attempt: {attempt + 1}/{max_attempts}")

            success, result = await captcha_solver.solve_geetest(
                page_url="https://app.galxe.com/quest",
                gt="244bcb8b9846215df5af4c624a750db4",
                challenge=action,
                init_params={
                    'captcha_id': '244bcb8b9846215df5af4c624a750db4',
                    'client_type': 'web',
                    'lang': 'en-us',
                },
                version=4
            )

            if success:
                logger.success(f"Account: {wallet_address} | Geetest captcha solved")
                return result

            raise ValueError(f"{result}")

        for attempt in range(max_attempts):
            try:
                if captcha_type == "geetest":
                    if not action:
                        raise ValueError("Geetest captcha requires 'action' parameter")
                    return await handle_geetest()
                else:
                    return await handle_turnistale()
            except Exception as e:
                logger.error(
                    f"Account: {wallet_address} | Error occurred while solving Cloudflare: {str(e)} | Retrying..."
                )
                if attempt == max_attempts - 1:
                    raise CaptchaSolvingFailed(f"Failed to solve Cloudflare after {max_attempts} attempts")

    async def process_request_tokens_from_faucet(self) -> OperationResult | None:
        max_attempts = config.attempts_and_delay_settings.max_faucet_attempts

        for attempt in range(max_attempts):
            api, wallet = None, None

            try:
                db_account_value = await self._ensure_db_account()

                if config.web3_settings.verify_balance:
                    wallet = Web3Wallet(
                        private_key=db_account_value.private_key,
                        rpc_url=config.web3_settings.irys_rpc_url,
                        proxy=db_account_value.active_account_proxy
                    )
                    balance = await wallet.human_balance()
                    if balance > 0:
                        logger.success(f"Account: {db_account_value.wallet_address} | Balance is sufficient ({balance} IRYS) | Skipped faucet")
                        return operation_success(self.account_data.private_key)

                api = IrysAPI(proxy=db_account_value.active_account_proxy)
                captcha_token = await self.get_captcha(wallet_address=db_account_value.wallet_address, captcha_type="cf")

                logger.info(f"Account: {db_account_value.wallet_address} | Requesting tokens from faucet")
                await api.call_faucet(captcha_token=captcha_token, wallet_address=db_account_value.wallet_address)

                logger.success(f"Account: {db_account_value.wallet_address} | Tokens requested")
                return operation_success(self.account_data.private_key)

            except APIError as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.wallet_address} | Max attempts reached, unable to request tokens from faucet | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                if error.error_type == APIErrorType.INVALID_CAPTCHA:
                    logger.error(f"Account: {self.account_data.wallet_address} | Invalid captcha token")
                    await self._update_account_proxy(attempt, max_attempts)
                    continue

                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during requesting tokens (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.private_key)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.wallet_address} | Max attempts reached, unable to request tokens from faucet | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during requesting tokens (Generic Exception): {error}")
                await self._update_account_proxy(attempt, max_attempts)

            finally:
                if api:
                    await api.close_session()

                if wallet:
                    await wallet.cleanup()


    async def process_top_up_game_balance(self):
        max_attempts = config.attempts_and_delay_settings.max_games_attempts

        for attempt in range(max_attempts):
            irys_games_module = None

            try:
                db_account_value = await self._ensure_db_account()

                logger.info(f"Account: {db_account_value.wallet_address} | Preparing to top up game balance..")
                irys_games_module = IrysGamesModule(
                    private_key=db_account_value.private_key,
                    rpc_url=config.web3_settings.irys_rpc_url,
                    proxy=db_account_value.active_account_proxy
                )

                if config.web3_settings.verify_balance:
                    play_balance = await irys_games_module.get_play_balance()
                    if play_balance > 0:
                        logger.success(f"Account: {db_account_value.wallet_address} | Play balance is sufficient ({play_balance} IRYS) | Skipped top-up")
                        return operation_success(self.account_data.private_key)

                top_up_amount = round(
                    random.uniform(
                        config.games_settings.top_up_amount.min,
                        config.games_settings.top_up_amount.max
                    ),
                    5
                )
                logger.info(f"Account: {db_account_value.wallet_address} | Topping up game balance with {top_up_amount} IRYS..")

                irys_balance = await irys_games_module.human_balance()
                if irys_balance < top_up_amount + 0.001:
                    logger.error(f"Account: {db_account_value.wallet_address} | Not enough IRYS balance to top up game balance | Available: {irys_balance} IRYS | Required: {top_up_amount + 0.001} IRYS | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                success, result = await irys_games_module.deposit_tokens(amount=top_up_amount)
                if success:
                    logger.success(f"Account: {db_account_value.wallet_address} | Game balance topped up | TX: {self.tx_hash_to_explorer_link(result)}")
                    return operation_success(self.account_data.private_key)
                else:
                    logger.error(f"Account: {db_account_value.wallet_address} | Failed to top up game balance: {result} | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

            except APIError as error:
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during balance top up (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.private_key)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.wallet_address} | Max attempts reached, unable to top up game balance | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during balance top up (Generic Exception): {error}")
                await self._update_account_proxy(attempt, max_attempts)

            finally:
                if irys_games_module:
                    await irys_games_module.cleanup()


    async def process_mint_omnihub_nft(self):
        max_attempts = config.attempts_and_delay_settings.max_faucet_attempts

        for attempt in range(max_attempts):
            irys_omnihub_module = None

            try:
                db_account_value = await self._ensure_db_account()

                logger.info(f"Account: {db_account_value.wallet_address} | Preparing to mint Omnihub NFT..")
                irys_omnihub_module = IrysOmnihubModule(
                    private_key=db_account_value.private_key,
                    rpc_url=config.web3_settings.irys_rpc_url,
                    proxy=db_account_value.active_account_proxy
                )

                balance = await irys_omnihub_module.human_balance()
                if balance < 0.001:
                    logger.error(f"Account: {db_account_value.wallet_address} | Not enough IRYS balance to mint NFT | Available: {balance} IRYS | Required at least: 0.001 IRYS | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                logger.info(f"Account: {db_account_value.wallet_address} | Minting Omnihub NFT..")
                success, result = await irys_omnihub_module.mint_nft()
                if success:
                    logger.success(f"Account: {db_account_value.wallet_address} | Omnihub NFT minted | TX: {self.tx_hash_to_explorer_link(result)}")
                    return operation_success(self.account_data.private_key)
                else:
                    logger.error(f"Account: {db_account_value.wallet_address} | Failed to mint Omnihub NFT: {result} | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

            except APIError as error:
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during minting Omnihub NFT (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.private_key)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.wallet_address} | Max attempts reached, unable to mint Omnihub NFT | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during minting Omnihub NFT (Generic Exception): {error}")
                await self._update_account_proxy(attempt, max_attempts)

            finally:
                if irys_omnihub_module:
                    await irys_omnihub_module.cleanup()


    async def process_wait_for_balance(self):
        TIME_LIMIT = 60
        CHECK_INTERVAL = 10
        wallet = None

        try:
            db_account_value = await self._ensure_db_account()

            logger.info(f"Account: {db_account_value.wallet_address} | Waiting for IRYS balance..")
            wallet = Web3Wallet(
                private_key=db_account_value.private_key,
                rpc_url=config.web3_settings.irys_rpc_url,
                proxy=db_account_value.active_account_proxy
            )

            start = time.monotonic()
            while True:
                balance = await wallet.human_balance()
                if balance > 0:
                    logger.success(f"Account: {db_account_value.wallet_address} | Balance sufficient ({balance} IRYS) | Continuing..")
                    return operation_success(self.account_data.private_key)

                elapsed = time.monotonic() - start
                if elapsed >= TIME_LIMIT:
                    logger.error(f"Account: {db_account_value.wallet_address} | Timed out after {TIME_LIMIT}s waiting for IRYS balance")
                    return operation_failed(self.account_data.private_key)

                remaining = int(TIME_LIMIT - elapsed)
                sleep_for = min(CHECK_INTERVAL, remaining)
                logger.info(f"Account: {db_account_value.wallet_address} | Balance insufficient ({balance} IRYS) | Checking again in {int(sleep_for)}s..")
                await asyncio.sleep(sleep_for)

        except Exception as error:
            error = validate_error(error)
            logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during waiting for balance (Generic Exception): {error} | Skipped permanently")
            return operation_failed(self.account_data.private_key)

        finally:
            if wallet:
                await wallet.cleanup()

    async def process_all_in_one(self) -> OperationResult | None:

        try:
            db_account_value = await self._ensure_db_account()

            for task in config.all_in_one_settings.tasks_to_perform:
                if task == "faucet":
                    operation_result = await self.process_request_tokens_from_faucet()
                elif task == "wait_for_balance":
                    operation_result = await self.process_wait_for_balance()
                elif task == "top_up_game_balance":
                    operation_result = await self.process_top_up_game_balance()
                elif task == "mint_omnihub_nft":
                    operation_result = await self.process_mint_omnihub_nft()
                elif task == "play_games":
                    operation_result = await self.process_play_games()
                else:
                    logger.error(f"Account: {db_account_value.wallet_address} | Unknown task: {task} | Skipped")
                    continue

                if operation_result is None or operation_result.get("status") is False:
                    logger.error(f"Account: {db_account_value.wallet_address} | Task {task} failed | Skipped permanently")
                    return operation_failed(self.account_data.private_key)
                else:
                    logger.success(f"Account: {db_account_value.wallet_address} | Task {task} completed")

                is_last_task = task == config.all_in_one_settings.tasks_to_perform[-1]
                if is_last_task:
                    break

                delay = random.randint(
                    config.attempts_and_delay_settings.delay_between_tasks.min,
                    config.attempts_and_delay_settings.delay_between_tasks.max
                )
                logger.info(f"Account: {db_account_value.wallet_address} | Waiting {delay}s before starting the next task..")
                await asyncio.sleep(delay)

            logger.success(f"Account: {db_account_value.wallet_address} | All tasks completed")
            return operation_success(self.account_data.private_key)

        except Exception as error:
            error = validate_error(error)
            logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during completing tasks (Generic Exception): {error} | Skipped permanently")
            return operation_failed(self.account_data.private_key)

    async def process_play_games(self):
        max_attempts = config.attempts_and_delay_settings.max_games_attempts
        completed_games = []

        for attempt in range(max_attempts):
            irys_games_module = None

            try:
                db_account_value = await self._ensure_db_account()

                logger.info(f"Account: {db_account_value.wallet_address} | Preparing to play games..")
                irys_games_module = IrysGamesModule(
                    private_key=db_account_value.private_key,
                    rpc_url=config.web3_settings.irys_rpc_url,
                    proxy=db_account_value.active_account_proxy
                )
                api = IrysAPI(proxy=db_account_value.active_account_proxy)

                play_balance = await irys_games_module.get_play_balance()
                if play_balance > 0:
                    logger.success(f"Account: {db_account_value.wallet_address} | Play balance is sufficient ({play_balance} IRYS)")
                else:
                    logger.info(f"Account: {db_account_value.wallet_address} | Play balance is insufficient ({play_balance} IRYS) | Please top up the game balance first | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                random.shuffle(config.games_settings.games_to_play)
                for game in config.games_settings.games_to_play:
                    if game in completed_games:
                        logger.info(f"Account: {db_account_value.wallet_address} | Game {game} already completed | Skipped")
                        continue

                    if game == "spritetype":
                        await self.execute_sprite_type_game(api=api, db_account_value=db_account_value)
                        completed_games.append(game)
                    else:
                        await self.execute_game(
                            game=game,
                            api=api,
                            irys_games_module=irys_games_module,
                            db_account_value=db_account_value
                        )
                        completed_games.append(game)

                    delay = random.randint(
                        config.attempts_and_delay_settings.delay_for_game.min,
                        config.attempts_and_delay_settings.delay_for_game.max
                    )
                    logger.info(f"Account: {db_account_value.wallet_address} | Waiting {delay}s before starting the next game..")
                    await asyncio.sleep(delay)

                logger.success(f"Account: {db_account_value.wallet_address} | All games completed")
                return operation_success(self.account_data.private_key)

            except APIError as error:
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during playing games (APIError): {error} | Skipped permanently")
                return operation_failed(self.account_data.private_key)

            except Exception as error:
                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    logger.error(f"Account: {self.account_data.wallet_address} | Max attempts reached, unable to play games | Skipped permanently")
                    return operation_failed(self.account_data.private_key)

                error = validate_error(error)
                logger.error(f"Account: {self.account_data.wallet_address} | Error occurred during playing games (Generic Exception): {error}")
                await self._update_account_proxy(attempt, max_attempts)

            finally:
                if irys_games_module:
                    await irys_games_module.cleanup()


    async def execute_game(self, game: GameType, api: IrysAPI, irys_games_module: IrysGamesModule, db_account_value: Accounts) -> None:
        logger.info(f"Account: {db_account_value.wallet_address} | Starting game: {game}")
        message, signature, initial_ts = await irys_games_module.authorize_payment()
        session_id = generate_session_id(initial_ts)

        response = await api.start_game(
            player_address=db_account_value.wallet_address,
            signature=signature,
            message=message,
            timestamp=initial_ts,
            session_id=session_id,
            game_type=game
        )

        tx = self.tx_hash_to_explorer_link(response["transactionHash"])
        delay = random.randint(
            config.attempts_and_delay_settings.delay_for_game.min,
            config.attempts_and_delay_settings.delay_for_game.max
        )

        logger.info(f"Account: {db_account_value.wallet_address} | Game {game} started | TX: {tx} | Waiting {delay}s before finishing..")
        await asyncio.sleep(delay)

        if game == "snake":
            score = random.randint(1000, 1500)
        elif game == "missile":
            score = random.randint(1600000, 2000000)
        elif game == "asteroids":
            score = random.randint(500000, 800000)
        else:
            score = random.randint(65000, 100000)

        message, signature, complete_at_ts = await irys_games_module.complete_payment(
            initial_ts=initial_ts,
            score=score,
            session_id=session_id,
            game_type=game
        )

        response = await api.complete_game(
            player_address=db_account_value.wallet_address,
            signature=signature,
            message=message,
            timestamp=complete_at_ts,
            session_id=session_id,
            game_type=game,
            score=score
        )

        tx = self.tx_hash_to_explorer_link(response["transactionHash"])
        logger.success(f"Account: {db_account_value.wallet_address} | Game {game} completed | Score: {score} | TX: {tx}")


    @staticmethod
    async def execute_sprite_type_game(api: IrysAPI, db_account_value: Accounts):
        logger.info(f"Account: {db_account_value.wallet_address} | Starting game: Sprite Type")
        game_stats = generate_sprite_game_stats()

        anti_cheat_hash = generate_anti_cheat_hash(db_account_value.wallet_address, game_stats)
        logger.info(f"Account: {db_account_value.wallet_address} | Generated game stats and anti-cheat hash")

        logger.info(f"Account: {db_account_value.wallet_address} | Submitting game results to the server..")
        ts = int(time.time() * 1000)
        response = await api.submit_result_for_sprite_type(
            wallet_address=db_account_value.wallet_address,
            game_stats=game_stats,
            anti_cheat_hash=anti_cheat_hash,
            timestamp=ts
        )

        url = response["url"]
        logger.success(f"Account: {db_account_value.wallet_address} | Sprite Type game completed | View results at: {url}")
