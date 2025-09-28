from eth_typing import HexStr

from core.onchain.wallet import Web3Wallet
from web3.types import TxParams


class IrysOmnihubModule(Web3Wallet):
    def __init__(self, private_key: str, rpc_url: str, proxy: str = None):
        super().__init__(private_key, rpc_url, proxy)
        self.proxy = proxy

    async def mint_nft(self):
        try:
            data = HexStr("0xa25ffea800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000800000000000000000000000000000000000000000000000000000000000000000")
            to = "0x2E7eaC00E4c7D971A974918E3d4b8484Ea6f257e"

            nonce = await self.eth.get_transaction_count(self.wallet_address)
            chain_id = await self.eth.chain_id

            value = self.to_wei(0.001, "ether")
            gas_price = await self.eth.gas_price

            tx: TxParams = {
                "from": self.wallet_address,
                "to": to,
                "value": value,
                "data": data,
                "nonce": nonce,
                "chainId": chain_id,
                "gasPrice": gas_price,
            }

            gas_estimate = await self.eth.estimate_gas(tx)
            tx["gas"] = int(gas_estimate * 1.2)

            await self.check_trx_availability(tx)
            return await self._process_transaction(tx)

        except Exception as error:
            return False, str(error)
