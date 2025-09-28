import asyncio
import json

from typing import Literal

from curl_cffi.requests import AsyncSession, Response

from models import GameType
from core.exceptions.base import APIError, ServerError, ProxyForbidden, RateLimitExceeded


class APIClient:
    def __init__(self, proxy: str = None):
        self.proxy = proxy
        self.session = self._create_session()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    def _create_session(self) -> AsyncSession:
        session = AsyncSession(impersonate="chrome131", verify=False)
        session.timeout = 60

        if self.proxy:
            session.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }

        return session

    async def clear_request(self, url: str) -> Response:
        session = self._create_session()
        return await session.get(url, allow_redirects=True, verify=False)

    @staticmethod
    async def _verify_response(response_data: dict | list) -> None:
        if isinstance(response_data, dict):

            if "success" in str(response_data):
                if response_data.get("success") is False:
                    raise APIError(
                        f"API returned an error: {response_data}", response_data
                    )

            elif "error" in str(response_data):
                if response_data.get("error", {}):
                    raise APIError(
                        f"API returned an error: {response_data}", response_data
                    )

    async def close_session(self) -> None:
        try:
            await self.session.close()
        except:
            pass

    async def send_request(
        self,
        url: str,
        request_type: Literal["POST", "GET", "OPTIONS", "PATCH"] = "POST",
        json_data: dict = None,
        params: dict = None,
        headers: dict = None,
        cookies: dict = None,
        verify: bool = True,
        max_retries: int = 2,
        retry_delay: float = 3.0,
    ) -> dict | Response:
        for attempt in range(max_retries):
            try:
                if request_type == "POST":
                    response = await self.session.post(
                        url,
                        json=json_data,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )
                elif request_type == "OPTIONS":
                    response = await self.session.options(
                        url,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )

                elif request_type == "PATCH":
                    response = await self.session.patch(
                        url,
                        json=json_data,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )

                else:
                    response = await self.session.get(
                        url,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )

                if verify:
                    if response.headers.get("ratelimit-remaining") and response.headers.get("ratelimit-reset"):
                        reset_time = int(response.headers.get("ratelimit-reset"))
                        remaining = int(response.headers.get("ratelimit-remaining"))
                        if remaining in [0, 1]:
                            raise RateLimitExceeded(reset_time)

                    if response.status_code == 403 and "403 Forbidden" in response.text:
                        raise ProxyForbidden(f"Proxy forbidden - {response.status_code}")

                    elif response.status_code == 403:
                        raise Exception(f"Response forbidden - 403: {response.text[:200]}")

                    elif response.status_code == 429:
                        raise APIError(f"Rate limit exceeded - {response.status_code}", response.text)

                    if response.status_code in (500, 502, 503, 504):
                        raise ServerError(f"Server error - {response.status_code}")

                    try:
                        response_json = response.json()
                        await self._verify_response(response_json)
                        return response_json
                    except json.JSONDecodeError:
                        raise Exception(f"Failed to decode response, most likely server error")

                return response

            except ServerError as error:
                if attempt == max_retries - 1:
                    raise error
                await asyncio.sleep(retry_delay)

            except (APIError, ProxyForbidden, RateLimitExceeded):
                raise

            except Exception as error:
                if attempt == max_retries - 1:
                    raise Exception(
                        f"Failed to send request after {max_retries} attempts: {error}"
                    )
                await asyncio.sleep(retry_delay)

        raise Exception(f"Failed to send request after {max_retries} attempts")


class IrysAPI(APIClient):
    def __init__(self, proxy: str = None, auth_token: str = None):
        super().__init__(proxy)
        self.auth_token = auth_token


    async def call_faucet(self, captcha_token: str, wallet_address: str) -> dict:
        headers = {
            'accept': '*/*',
            'accept-language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'origin': 'https://irys.xyz',
            'referer': 'https://irys.xyz/faucet',
            'user-agent': self.user_agent,
        }

        json_data = {
            'captchaToken': captcha_token,
            'walletAddress': wallet_address,
        }

        return await self.send_request(
            url="https://irys.xyz/api/faucet",
            request_type="POST",
            json_data=json_data,
            headers=headers,
        )


    async def start_game(self, player_address: str, signature: str, message: str, timestamp: int, session_id: str, game_type: GameType, game_cost: float = 0.001) -> dict:
        headers = {
            'accept': '*/*',
            'accept-language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'origin': 'https://play.irys.xyz',
            'referer': f'https://play.irys.xyz/{game_type}',
            'user-agent': self.user_agent,
        }

        json_data = {
            'playerAddress': player_address,
            'gameCost': game_cost,
            'signature': signature,
            'message': message,
            'timestamp': timestamp,
            'sessionId': session_id,
            'gameType': game_type,
        }

        response = await self.send_request(
            url="https://play.irys.xyz/api/game/start",
            request_type="POST",
            json_data=json_data,
            headers=headers,
        )

        return response["data"]

    async def complete_game(self, player_address: str, score: int, signature: str, message: str, timestamp: int, session_id: str, game_type: GameType) -> dict:
        headers = {
            'Referer': f'https://play.irys.xyz/{game_type}',
            'User-Agent': self.user_agent,
            'Content-Type': 'application/json',
        }

        json_data = {
            'playerAddress': player_address,
            'gameType': game_type,
            'score': score,
            'signature': signature,
            'message': message,
            'timestamp': timestamp,
            'sessionId': session_id,
        }

        response = await self.send_request(
            url="https://play.irys.xyz/api/game/complete",
            request_type="POST",
            json_data=json_data,
            headers=headers,
        )

        return response["data"]


    async def submit_result_for_sprite_type(self, wallet_address: str, game_stats: dict, anti_cheat_hash: str, timestamp: int) -> dict:
        headers = {
            'accept': '*/*',
            'accept-language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'origin': 'https://spritetype.irys.xyz',
            'referer': 'https://spritetype.irys.xyz/',
            'user-agent': self.user_agent,
        }

        json_data = {
            'walletAddress': wallet_address,
            'gameStats': game_stats,
            'antiCheatHash': anti_cheat_hash,
            'timestamp': timestamp,
        }

        response = await self.send_request(
            url="https://spritetype.irys.xyz/api/submit-result",
            request_type="POST",
            json_data=json_data,
            headers=headers,
        )

        return response["data"]
