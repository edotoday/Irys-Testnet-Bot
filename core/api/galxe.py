import asyncio
import json
import uuid

from typing import Literal
from curl_cffi.requests import AsyncSession, Response

from utils.processing.handlers import require_auth_token
from core.exceptions.base import APIError, ServerError, ProxyForbidden, RateLimitExceeded


class APIClient:
    API_URL = "https://graphigo.prd.galaxy.eco/query"

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
    async def _verify_response(response_data: dict | list, api_type: str = None) -> None:
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
        method: str = None,
        url: str = None,
        request_type: Literal["POST", "GET", "OPTIONS", "PATCH"] = "POST",
        json_data: dict = None,
        params: dict = None,
        headers: dict = None,
        cookies: dict = None,
        verify: bool = True,
        max_retries: int = 2,
        retry_delay: float = 3.0,
    ) -> dict | Response:
        if not method and not url:
            url = self.API_URL

        if method:
            url = f"{self.API_URL}/{method}"

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


class GalxeAPI(APIClient):
    def __init__(self, proxy: str = None, auth_token: str = None):
        super().__init__(proxy)
        self.auth_token = auth_token
        self.base_headers = {
            'accept': '*/*',
            'accept-language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'origin': 'https://app.galxe.com',
            'platform': 'web',
            'request-id': str(uuid.uuid4()),
            'user-agent': self.user_agent,
        }

    def _get_headers(self, with_auth: bool = True) -> dict:
        headers = self.base_headers.copy()
        if with_auth and self.auth_token:
            headers['authorization'] = self.auth_token
        return headers

    async def is_galaxe_id_exist(self, wallet_address: str) -> bool:
        json_data = {
            'operationName': 'GalxeIDExist',
            'variables': {
                'schema': f'EVM:{wallet_address}',
            },
            'query': 'query GalxeIDExist($schema: String!) {\n  galxeIdExist(schema: $schema)\n}',
        }

        response = await self.send_request(
            request_type="POST",
            json_data=json_data,
            headers=self._get_headers(with_auth=False),
        )

        return response["data"]["galxeIdExist"]


    async def sign_in(self, signature: str, message: str, wallet_address: str) -> str:
        json_data = {
            'operationName': 'SignIn',
            'variables': {
                'input': {
                    'address': wallet_address,
                    'signature': signature,
                    'message': message,
                    'addressType': 'EVM',
                    'publicKey': '1',
                },
            },
            'query': 'mutation SignIn($input: Auth) {\n  signin(input: $input)\n}',
        }

        response = await self.send_request(
            request_type="POST",
            json_data=json_data,
            headers=self._get_headers(with_auth=False),
        )

        return response["data"]["signin"]


    @require_auth_token
    async def create_new_account(self, wallet_address: str, username: str) -> str:
        json_data = {
            'operationName': 'CreateNewAccount',
            'variables': {
                'input': {
                    'schema': f'EVM:{wallet_address}',
                    'socialUsername': username,
                    'username': username,
                },
            },
            'query': 'mutation CreateNewAccount($input: CreateNewAccount!) {\n  createNewAccount(input: $input)\n}',
        }

        response = await self.send_request(
            request_type="POST",
            json_data=json_data,
            headers=self._get_headers(),
        )

        return response["data"]["createNewAccount"]


    @require_auth_token
    async def sync_credential_value(self, wallet_address: str, credential_id: str, answers: list[str]) -> dict:
        json_data = {
            'operationName': 'SyncCredentialValue',
            'variables': {
                'input': {
                    'syncOptions': {
                        'credId': credential_id,
                        'address': f'EVM:{wallet_address}',
                        'quiz': {
                            'answers': answers,
                        },
                    },
                },
            },
            'query': 'mutation SyncCredentialValue($input: SyncCredentialValueInput!) {\n  syncCredentialValue(input: $input) {\n    value {\n      address\n      spaceUsers {\n        follow\n        points\n        participations\n        __typename\n      }\n      campaignReferral {\n        count\n        __typename\n      }\n      galxePassport {\n        eligible\n        lastSelfieTimestamp\n        __typename\n      }\n      spacePoint {\n        points\n        __typename\n      }\n      spaceParticipation {\n        participations\n        __typename\n      }\n      gitcoinPassport {\n        score\n        lastScoreTimestamp\n        __typename\n      }\n      walletBalance {\n        balance\n        __typename\n      }\n      multiDimension {\n        value\n        __typename\n      }\n      allow\n      survey {\n        answers\n        __typename\n      }\n      quiz {\n        allow\n        correct\n        __typename\n      }\n      prediction {\n        isCorrect\n        __typename\n      }\n      spaceFollower {\n        follow\n        __typename\n      }\n      __typename\n    }\n    message\n    __typename\n  }\n}',
        }

        response = await self.send_request(
            request_type="POST",
            json_data=json_data,
            headers=self._get_headers(),
        )

        return response["data"]["syncCredentialValue"]


    async def add_typed_credential_items(self, wallet_address: str, credential_id: str, campaign_id: str, captcha: dict) -> dict:
        json_data = {
            'operationName': 'AddTypedCredentialItems',
            'variables': {
                'input': {
                    'credId': credential_id,
                    'campaignId': campaign_id,
                    'operation': 'APPEND',
                    'items': [
                        f'EVM:{wallet_address}',
                    ],
                    'captcha': captcha,
                },
            },
            'query': 'mutation AddTypedCredentialItems($input: MutateTypedCredItemInput!) {\n  typedCredentialItems(input: $input) {\n    id\n    __typename\n  }\n}',
        }

        response = await self.send_request(
            request_type="POST",
            json_data=json_data,
            headers=self._get_headers(),
        )

        return response["data"]["typedCredentialItems"]