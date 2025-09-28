from loguru import logger
from tortoise import Tortoise
from tortoise.backends import asyncpg

from loader import config
from sys import exit

import tortoise.backends
import asyncpg.pgproto.pgproto
import asyncpg.pgproto



async def initialize_database() -> None:
    print(type(asyncpg), type(asyncpg.pgproto), type(asyncpg.pgproto.pgproto))

    try:
        try:
            await Tortoise.close_connections()
        except:
            pass

        await Tortoise.init(
            db_url=config.application_settings.database_url,
            modules={"models": ["database.models.accounts"]},
            timezone="UTC",
        )

        await Tortoise.generate_schemas(safe=True)

    except Exception as error:
        logger.error(f"Error while initializing database: {error}")

        try:
            await Tortoise.close_connections()
        except Exception as close_error:
            logger.error(f"Error while closing database connections: {close_error}")

        exit(1)
