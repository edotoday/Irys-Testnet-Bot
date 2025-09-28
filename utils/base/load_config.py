import os
import random

import yaml

from pathlib import Path
from typing import Dict, List, Generator, Union, Optional, Literal

from loguru import logger
from better_proxy import Proxy

from models import Account, Config
from sys import exit


class ConfigurationError(Exception):
    pass


class ConfigLoader:
    REQUIRED_PARAMS = frozenset(
        {
            "application_settings",
            "attempts_and_delay_settings",
        }
    )

    def __init__(self, base_path: Union[str, Path] = None):
        self.base_path = Path(base_path or os.getcwd())
        self.config_path = self.base_path / "config"
        self.data_path = self.config_path / "data"
        self.settings_path = self.config_path / "settings.yaml"

    @staticmethod
    def _read_file(file_path: Path, allow_empty: bool = False, is_yaml: bool = False) -> Union[List[str], Dict]:
        if not file_path.exists():
            raise ConfigurationError(f"File not found: {file_path}")

        try:
            if is_yaml:
                return yaml.safe_load(file_path.read_text(encoding='utf-8'))

            content = file_path.read_text(encoding='utf-8').strip()
            if not allow_empty and not content:
                raise ConfigurationError(f"File is empty: {file_path}")

            return [line.strip() for line in content.splitlines() if line.strip()]

        except Exception as e:
            raise ConfigurationError(f"Failed to read file {file_path}: {str(e)}")

    def _load_yaml(self) -> Dict:
        try:
            config = self._read_file(self.settings_path, is_yaml=True)
            missing_fields = self.REQUIRED_PARAMS - set(config.keys())

            if missing_fields:
                raise ConfigurationError(
                    f"Missing required fields: {', '.join(missing_fields)}"
                )
            return config

        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML format: {e}")

    def _parse_proxies(self) -> Optional[List[str]]:
        try:
            proxy_lines = self._read_file(
                self.data_path / "proxies.txt", allow_empty=True
            )
            for proxy in proxy_lines:
                Proxy.from_str(proxy)

            return [Proxy.from_str(proxy).as_url for proxy in proxy_lines]
        except Exception as e:
            raise ConfigurationError(f"Failed to parse proxies: {e}")

    def _parse_accounts(
            self,
            filename: str,
    ) -> Generator[Account | str, None, None]:
        try:
            lines = self._read_file(self.data_path / filename, allow_empty=True)

            for line in lines:
                try:
                    line = line.strip()
                    if not line:
                        continue

                    yield Account(private_key=line)

                except (ValueError, IndexError) as e:
                    logger.warning(f"Invalid account format: {line} | File: {filename} | Error: {e}")
                    exit(1)

        except ConfigurationError:
            raise

        except Exception as e:
            raise ConfigurationError(f"Failed to process accounts file: {str(e)} | File: {filename}")

    def load(self) -> Config | None:
        try:
            params = self._load_yaml()
            proxies = self._parse_proxies()

            accounts_to_request_tokens = list(self._parse_accounts("request_tokens_accounts.txt"))
            accounts_to_top_up_game_balance = list(self._parse_accounts("top_up_game_balance_accounts.txt"))
            accounts_to_play_games = list(self._parse_accounts("play_games_accounts.txt"))
            accounts_to_mint_nft = list(self._parse_accounts("mint_nft_accounts.txt"))
            accounts_for_all_in_one = list(self._parse_accounts("all_in_one_accounts.txt"))

            if not any([
                accounts_to_request_tokens,
                accounts_to_top_up_game_balance,
                accounts_to_play_games,
                accounts_to_mint_nft,
                accounts_for_all_in_one
            ]):
                raise ConfigurationError("No accounts found in files: request_tokens_accounts.txt, top_up_game_balance.txt, play_games_accounts.txt, mint_nft_accounts.txt, all_in_one_accounts.txt | Please add accounts to the files")

            return Config(
                **params,
                accounts_to_request_tokens=accounts_to_request_tokens,
                accounts_to_top_up_game_balance=accounts_to_top_up_game_balance,
                accounts_to_play_games=accounts_to_play_games,
                accounts_to_mint_nft=accounts_to_mint_nft,
                accounts_for_all_in_one=accounts_for_all_in_one,
                proxies=proxies,
            )

        except Exception as e:
            logger.error(f"Configuration loading failed: {e}")
            exit(1)


def load_config() -> Config:
    return ConfigLoader().load()
