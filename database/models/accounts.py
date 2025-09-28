import asyncio
import random

import pytz
from datetime import datetime
from tortoise import Model, fields
from tortoise.expressions import Q


class Accounts(Model):
    wallet_address = fields.CharField(max_length=255, unique=True)
    private_key = fields.CharField(max_length=1024)
    active_account_proxy = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "irys_accounts"

    @classmethod
    async def get_account(cls, wallet_address: str):
        return await cls.get_or_none(wallet_address=wallet_address)

    @classmethod
    async def get_accounts(cls):
        return await cls.all()

    async def update_account_proxy(self, proxy: str):
        self.active_account_proxy = proxy
        await self.save()

    @classmethod
    async def get_account_proxy(cls, wallet_address: str) -> str:
        account = await cls.get_account(wallet_address=wallet_address)
        return account.active_account_proxy if account else ""

    @classmethod
    async def create_account(
        cls,
        wallet_address: str,
        private_key: str,
        active_account_proxy: str = None
    ) -> "Accounts":
        account = await cls.get_account(wallet_address=wallet_address)
        if account is None:
            account = await cls.create(
                wallet_address=wallet_address,
                private_key=private_key,
                active_account_proxy=active_account_proxy,
            )
        else:
            if private_key is not None:
                account.private_key = private_key
            if active_account_proxy is not None:
                account.active_account_proxy = active_account_proxy
            await account.save()

        return account

    async def update_account(
            self,
            active_account_proxy: str = None,
    ):
        if active_account_proxy is not None:
            self.active_account_proxy = active_account_proxy

        await self.save()
        return self

    @classmethod
    async def delete_account(cls, wallet_address: str) -> bool:
        account = await cls.get_account(wallet_address=wallet_address)
        if account is None:
            return False
        await account.delete()
        return True

    @classmethod
    async def clear_all_accounts_proxies(cls) -> int:
        accounts = await cls.all()

        async def clear_proxy(account: Accounts):
            account.active_account_proxy = None
            await account.save()

        tasks = [asyncio.create_task(clear_proxy(account)) for account in accounts]
        await asyncio.gather(*tasks)

        return len(accounts)
