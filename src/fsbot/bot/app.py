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


async def _heartbeat(path) -> None:
    """Отметка «цикл опроса жив».

    Бот однажды молча лёг с кодом 0, и заметил это человек, а не система: снаружи
    работающий и упавший процесс выглядят одинаково. Свежесть этого файла проверяет
    healthcheck контейнера.
    """
    while True:
        path.touch()
        await asyncio.sleep(HEARTBEAT_PERIOD)


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
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
