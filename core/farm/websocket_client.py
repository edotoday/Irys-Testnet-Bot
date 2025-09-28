import asyncio
import base64
import json
import os
import random
import ssl
import time

import aiohttp
from aiohttp import WSMsgType, ClientSession, ClientConnectionError, ClientConnectorSSLError, ClientWebSocketResponse, ClientTimeout
from loguru import logger

from loader import config
from models import Account

from core.exceptions.tracker import ErrorTracker, TooManyErrorsException
from core.bot.base import Bot
from database import Accounts
from utils import WebSocketStats


class WebSocketClient:
    WSS_URL = "wss://ws.sixpence.ai/"
    COMMON_ERRORS = [
        "Expectation Failed",
        "Cannot connect to host",
        "Invalid response status",
        "Connection timeout to host",
        "Service Unavailable",
        "Internal Server Error",
    ]

    def __init__(self, account: Accounts, process_id: int, ws_stats: WebSocketStats):
        self.process_id = process_id
        self.account = account
        self.bot = Bot(Account(private_key=self.account.private_key))

        self.error_tracker = ErrorTracker(max_errors=3, time_window=120)
        self.ws: ClientWebSocketResponse | None = None
        self.session_token: str = ""

        self.ws_stats = ws_stats
        self.is_ws_counted = 0
        self.last_auth_data = {}

        self._tasks = []
        self._shutdown_event = asyncio.Event()

    def count_ws(self, inc: bool = True) -> None:
        if inc and self.is_ws_counted == 0:
            self.ws_stats.inc()
            self.is_ws_counted = True
        elif not inc and self.is_ws_counted == 1:
            self.ws_stats.dec()
            self.is_ws_counted = False

    @staticmethod
    def _setup_ssl_context() -> ssl.SSLContext:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    @staticmethod
    def _setup_headers() -> dict:
        random_bytes = os.urandom(16)
        sec_websocket_key = base64.b64encode(random_bytes).decode('utf-8')
        return {
            'Host': 'ws.sixpence.ai',
            'Connection': 'Upgrade',
            'Upgrade': 'websocket',
            'Sec-WebSocket-Version': '13',
            'Sec-WebSocket-Key': sec_websocket_key,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
            'Origin': 'chrome-extension://bcakokeeafaehcajfkajcpbdkfnoahlh',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'uk,en-US;q=0.9,en;q=0.8,ru;q=0.7',
        }

    async def _shutdown(self, session: aiohttp.ClientSession) -> None:
        try:
            tasks = list(self._tasks)
            for t in tasks:
                if not t.done() and not t.cancelled():
                    t.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

            if session:
                try:
                    await session.close()
                except Exception as e:
                    logger.warning(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Exception while closing session: {e}")
                finally:
                    self.ws = None

        except Exception as e:
            logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Exception in _shutdown: {e}")

        finally:
            self._tasks.clear()


    async def _wait_for_session_token(self, timeout: int = 300) -> bool:
        logger.info(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Waiting for session token...")
        start_time = time.time()
        while not self.session_token:
            if time.time() - start_time > timeout:
                return False
            await asyncio.sleep(1)
        return True

    async def connect(self) -> None:
        while True:
            self._shutdown_event.clear()

            try:
                if self.last_auth_data and (time.time() - self.last_auth_data["timestamp"] < 120):
                    signature = self.last_auth_data["signature"]
                    message = self.last_auth_data["message"]
                else:
                    signature, message = await self.bot.process_extension_auth(self.account, self.process_id)
                    self.last_auth_data = {"signature": signature, "message": message, "timestamp": time.time()}

                async with ClientSession(timeout=ClientTimeout(30), proxy=self.account.active_account_proxy) as session:
                    async with session.ws_connect(
                            url=self.WSS_URL,
                            headers=self._setup_headers(),
                            ssl=self._setup_ssl_context(),
                            proxy=self.account.active_account_proxy,
                            timeout=None,
                            autoping=False,
                            receive_timeout=None,
                            # heartbeat=30
                    ) as ws:
                        self.ws: ClientWebSocketResponse = ws

                        self._tasks.append(asyncio.create_task(self.handle_ws_message()))
                        await self.send_extension_auth(self.account.wallet_address, message, signature)
                        result = await self._wait_for_session_token()

                        if not result:
                            delay = random.randint(
                                config.attempts_and_delay_settings.delay_before_restart_websocket_connection.min,
                                config.attempts_and_delay_settings.delay_before_restart_websocket_connection.max
                            )
                            logger.warning(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Cant receive session token within timeout | Restarting connection in {delay} seconds")
                            await asyncio.sleep(delay)
                            self._shutdown_event.set()
                            continue

                        logger.success(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Session token received | WebSocket connection established")
                        self._tasks.append(asyncio.create_task(self.keepalive_heartbeat()))
                        await self._shutdown_event.wait()

                        for task in self._tasks:
                            if not task.done():
                                continue

                            exc = task.exception()
                            if isinstance(exc, TooManyErrorsException):
                                raise exc

            except asyncio.CancelledError:
                self.count_ws(False)
                logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | WebSocket connection cancelled")
                raise

            except TooManyErrorsException:
                self.count_ws(False)
                raise

            except (ConnectionResetError, ClientConnectionError, ClientConnectorSSLError) as error:
                self.count_ws(False)
                logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | WebSocket connection error: {error}")
                try:
                    self.error_tracker.add_error(error)
                except TooManyErrorsException:
                    raise

            except asyncio.TimeoutError as error:
                self.count_ws(False)
                try:
                    self.error_tracker.add_error(error)
                except TooManyErrorsException:
                    raise

            except Exception as error:
                self.count_ws(False)
                error_str = str(error)

                if not any(err_msg in error_str for err_msg in self.COMMON_ERRORS):
                    logger.error(f'Process: {self.process_id} | Account: {self.account.wallet_address} | Unexpected websocket error: {error}')

                try:
                    self.error_tracker.add_error(error)
                except TooManyErrorsException:
                    raise

            finally:
                await self._shutdown(session)
                await asyncio.sleep(2)

    async def handle_ws_message(self):
        try:
            async for msg in self.ws:

                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data["type"] == "extension_auth":
                        self.session_token = data["data"]["token"]
                        # logger.success(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Successfully authenticated with WebSocket | Session token received")
                    elif data["type"] == "extension_user_msg":
                        daily_points = round(data['data']['currentDayPoints'], 3)
                        total_points = round(data['data']['currentPoints'], 3)
                        await self.account.update_account(
                            daily_points=daily_points,
                            total_points=total_points
                        )

                        logger.info(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Daily points: {daily_points} | Total points: {total_points}")
                    else:
                        logger.warning(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Unknown message type received: {data['type']}: {data}")

        except (ConnectionResetError, ClientConnectionError):
            logger.info(f"Process: {self.process_id} | Account: {self.account.wallet_address} | WebSocket connection reset or closed")
            self._shutdown_event.set()

        except ClientConnectorSSLError:
            logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | SSL error in WebSocket connection | If there are many of these errors, try installing certificates")
            self._shutdown_event.set()

        except asyncio.TimeoutError:
            logger.info(f"Process: {self.process_id} | Account: {self.account.wallet_address} | WebSocket connection timed out")
            self._shutdown_event.set()

        except Exception as e:
            logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Error occurred while handling WebSocket message: {e}")
            self._shutdown_event.set()

    def handle_task_error(self, context: str, error: Exception):
        error_text = None
        if isinstance(error, ConnectionResetError) or isinstance(error, ClientConnectionError):
            error_text = "Connection reset or closed"
        elif isinstance(error, asyncio.TimeoutError):
            error_text = "WebSocket connection timed out"
        elif isinstance(error, ClientConnectorSSLError):
            error_text = "SSL error in WebSocket connection | If there are many of these errors, try installing certificates"
        else:
            error_str = str(error)
            if not any(err_msg in error_str for err_msg in self.COMMON_ERRORS):
                error_text = f'Unexpected websocket error: {error}'

        if error_text is not None:
            if isinstance(error, (ConnectionResetError, ClientConnectionError, asyncio.TimeoutError)):
                logger.info(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Context: {context} | {error_text}")
            else:
                logger.error(f"Process: {self.process_id} | Account: {self.account.wallet_address} | Context: {context} | {error_text}")

        try:
            self.error_tracker.add_error(error)
        except TooManyErrorsException:
            self._shutdown_event.set()
            raise

        self._shutdown_event.set()

    async def keepalive_heartbeat(self) -> None:
        self.count_ws(inc=True)
        while not self._shutdown_event.is_set():
            try:
                json_data = {
                  "type": "extension_heartbeat",
                  "token": self.session_token,
                  "address": self.account.wallet_address,
                  "taskEnable": False
                }
                await self.ws.send_json(json_data)
                logger.success(f"Process: {self.process_id} | Account: {self.account.wallet_address} is alive and farming")
                await asyncio.sleep(30)

            except Exception as error:
                self.handle_task_error("Heartbeat messages", error)
                break

    async def send_extension_auth(self, used_id: str, message: str, signature: str):
        json_data = {
            "type": "extension_auth",
            "data": {
                "userId": used_id,
                "message": message,
                "signature": signature
            }
        }
        await self.ws.send_json(json_data)
