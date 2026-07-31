"""Сборка и запуск бота: long polling, без публичного URL (решение 15)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from fsbot.bot.handlers import router
from fsbot.config import Config
from fsbot.fatsecret.client import FatSecretClient
from fsbot.llm.openrouter import OpenRouter
from fsbot.storage import Storage

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="что умеет бот"),
    BotCommand(command="help", description="как пользоваться"),
    BotCommand(command="link", description="привязать аккаунт FatSecret"),
    BotCommand(command="tz", description="часовой пояс"),
    BotCommand(command="undo", description="отменить последнюю запись"),
    BotCommand(command="attribution", description="об источнике данных"),
]

# /allow видит только владелец: иначе друзья пробуют админскую команду.
OWNER_COMMANDS = [*PUBLIC_COMMANDS, BotCommand(command="allow", description="открыть доступ")]


HEARTBEAT_PERIOD = 30


class PollingConflict(logging.Filter):
    """Ловит «Conflict: terminated by other getUpdates» из логов aiogram.

    Два процесса на одном токене — не сбой связи, а поломка развёртывания: Telegram
    раздаёт апдейты то одному, то другому, диалог рвётся на середине, и выглядит это
    как хаотичные ответы невпопад, а не как ошибка. Сам процесс при этом жив и здоров,
    поэтому healthcheck обязан узнать об этом от логов.
    """

    detected = False

    def filter(self, record: logging.LogRecord) -> bool:
        if "conflict" in record.getMessage().lower():
            PollingConflict.detected = True
        return True


async def _heartbeat(path) -> None:
    """Отметка «бот действительно обслуживает свой токен».

    Бот однажды молча лёг с кодом 0, и заметил это человек, а не система. При конфликте
    отметку перестаём обновлять: живой процесс, который ничего не получает, для
    пользователя неотличим от мёртвого, и healthcheck должен показывать то же самое.
    """
    while True:
        if PollingConflict.detected:
            log.error(
                "второй экземпляр бота на том же токене — апдейты уходят к нему; "
                "heartbeat остановлен, контейнер станет unhealthy"
            )
        else:
            path.touch()
        await asyncio.sleep(HEARTBEAT_PERIOD)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("aiogram.dispatcher").addFilter(PollingConflict())
    config = Config.from_env()

    storage = Storage(config.db_path)
    await storage.open()

    fatsecret = FatSecretClient(config.consumer_key, config.consumer_secret)
    llm = OpenRouter(config.openrouter_key, config.text_models, config.vision_models)

    bot = Bot(
        token=config.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    dispatcher.workflow_data.update(storage=storage, fs=fatsecret, llm=llm, cfg=config)

    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    await bot.set_my_commands(
        OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=config.owner_id)
    )

    me = await bot.me()
    log.info("бот @%s запущен, режим ограниченный (Basic: без штрих-кодов)", me.username)

    heartbeat = asyncio.create_task(_heartbeat(config.state_dir / "heartbeat"))

    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat.cancel()
        await llm.close()
        await fatsecret.close()
        await storage.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("остановлен")
