import random
import time

from typing import Optional
from eth_abi import encode
from eth_typing import HexStr

from core.onchain.wallet import Web3Wallet
from models.bot import GameType
from models.onchain import IrysGameContract
from web3.types import TxParams


class IrysGamesModule(Web3Wallet):
    def __init__(self, private_key: str, rpc_url: str, proxy: str = None):
        super().__init__(private_key, rpc_url, proxy)
        self.proxy = proxy

    async def get_play_balance(self) -> Optional[float]:
        contract_address = IrysGameContract.address
        method_id = "0x47734892"

        encoded_address = encode(
            ["address"],
            [self.to_checksum_address(self.wallet_address)]
        ).hex()
        data = method_id + encoded_address

        call: TxParams = {
            "to": self.to_checksum_address(contract_address),
            "data": HexStr(data),
        }

        raw_balance = await self.eth.call(call)
        balance = int(raw_balance.hex(), 16)

        return float(self.from_wei(balance, "ether"))


    async def deposit_tokens(self, amount: float) -> tuple[bool, str]:
        try:
            nonce = await self.eth.get_transaction_count(self.wallet_address)
            value = self.to_wei(amount, "ether")
            data = "0xd0e30db0"  # deposit()
            to = "0xBC41F2B6BdFCB3D87c3d5E8b37fD02C56B69ccaC"

            chain_id = await self.eth.chain_id

            tx: TxParams = {
                "from": self.wallet_address,
                "to": to,
                "value": value,
                "data": HexStr(data),
                "nonce": nonce,
                "chainId": chain_id,
                "gasPrice": await self.eth.gas_price,
            }

            gas_estimate = await self.eth.estimate_gas(tx)
            tx["gas"] = int(gas_estimate * 1.2)

            await self.check_trx_availability(tx)
            return await self._process_transaction(tx)

        except Exception as error:
            return False, str(error)


    async def authorize_payment(self) -> tuple[str, str, int]:
        ts = int(time.time() * 1000)
        message = f"I authorize payment of 0.001 IRYS to play a game on Irys Arcade.\n    \nPlayer: {self.wallet_address}\nAmount: 0.001 IRYS\nTimestamp: {ts}\n\nThis signature confirms I own this wallet and authorize the payment."

        signature = await self.get_signature(message)
        return message, signature, ts


    async def complete_payment(self, initial_ts: int, score: int, session_id: str, game_type: GameType) -> tuple[str, str, int]:
        complete_at_ts = initial_ts + random.randint(2, 10) * 60 * 1000
        message = f"I completed a {game_type} game on Irys Arcade.\n    \nPlayer: {self.wallet_address}\nGame: {game_type}\nScore: {score}\nSession: {session_id}\nTimestamp: {complete_at_ts}\n\nThis signature confirms I own this wallet and completed this game."

        signature = await self.get_signature(message)
        return message, signature, complete_at_ts

