import asyncio

from collections import deque
from loguru import logger


class SocialAccountsManager:
    def __init__(self):
        self.accounts = deque()
        self.used_accounts = []
        self.lock = asyncio.Lock()

    def load_tokens(self, accounts: list[str], used_accounts: list[str] = None):
        self.accounts = deque([account for account in accounts])
        if used_accounts:
            self.used_accounts = used_accounts

    async def get_token(self) -> str | None:
        async with self.lock:
            while True:
                if self.accounts:
                    token = self.accounts.popleft()
                    if token not in self.used_accounts:
                        return token

                else:
                    break

            while True:
                logger.critical(f"No available tokens or wallets. Please add more and restart the bot.")
                await asyncio.sleep(5)

    async def release_token(self, token: str):
        async with self.lock:
            self.accounts.append(token)

    async def remove_token(self, token: str):
        async with self.lock:
            try:
                self.accounts.remove(token)
                return True
            except ValueError:
                return False
