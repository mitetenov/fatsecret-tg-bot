"""Команды и диалог. Доступ — только по приглашению (решение 8)."""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from io import BytesIO
from zoneinfo import available_timezones

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    Message,
    ReplyKeyboardRemove,
)

from fsbot.bot import ui
from fsbot.bot.pipeline import (
    apply_candidate,
    build_draft,
    create_own_food,
    draft_from_food,
    render_report,
    set_amount,
    shift_day,
    write_draft,
)
from fsbot.config import Config
from fsbot.domain import barcodes
from fsbot.fatsecret.client import FatSecretClient, FatSecretError
from fsbot.llm.openrouter import LLMError, OpenRouter
from fsbot.storage import Storage

log = logging.getLogger(__name__)
router = Router()

BARCODE = re.compile(r"^\d{8,14}$")

HELP = """Пишу еду в твой дневник FatSecret.

<b>Как пользоваться</b>
• Текстом: «творог 5% 200г и овсянка 60г»
• Фото тарелки — оценю блюда и вес
• Фото упаковки — прочитаю название и найду продукт

Перед записью показываю, что нашёл, — жмёшь «Записать» или правишь.

<b>Команды</b>
/link — привязать аккаунт FatSecret
/tz — часовой пояс
/undo — отменить последнюю запись
/attribution — об источнике данных

• Штрих-кодом: пришли цифры под кодом или фото самого кода

Если продукта нет в базе — пришли фото этикетки, создам его в твоём аккаунте."""

ATTRIBUTION = """Данные о продуктах и дневник — <b>fatsecret</b>.
Powered by fatsecret · https://platform.fatsecret.com

Точную формулировку атрибуции нужно сверить с Terms and Conditions FatSecret —
пока здесь заглушка."""

BARCODE_UNKNOWN = """Такого штрих-кода нет в базе FatSecret.

Пришли фото упаковки с таблицей пищевой ценности — прочитаю КБЖУ, создам продукт
в твоём аккаунте и запомню этот код: в следующий раз хватит одного сканирования."""


class Link(StatesGroup):
    waiting_pin = State()
    waiting_tz = State()


class Edit(StatesGroup):
    waiting_amount = State()


class Barcode(StatesGroup):
    """Код не нашёлся — ждём фото этикетки, чтобы создать продукт и связать с кодом."""

    waiting_label = State()


def _llm_failure_text(exc: LLMError, what: str) -> str:
    """Перегрузка провайдера и непонятая еда — разные беды, и советы разные."""
    if exc.rate_limited:
        return (
            "Модель сейчас перегружена на стороне провайдера — попробуй ещё раз через "
            "минуту. Переписывать ничего не нужно, дело не в тебе."
        )
    if what == "фото":
        return "Не смог разобрать фото. Напиши текстом, что съел."
    return "Не смог разобрать. Напиши иначе — например «овсянка 60 г»."


async def _gate(message: Message, storage: Storage, cfg: Config) -> bool:
    """Пускаем владельца и приглашённых; остальным — их id, чтобы попросить доступ."""
    user_id = message.from_user.id
    user = await storage.ensure_user(user_id)
    if user_id == cfg.owner_id or user.allowed:
        return True
    await message.answer(
        f"Бот личный, доступ по приглашению.\nТвой id: <code>{user_id}</code>"
    )
    return False


async def _linked(message: Message, storage: Storage):
    user = await storage.get_user(message.from_user.id)
    if user and user.is_linked:
        return user
    await message.answer("Сначала привяжи аккаунт FatSecret: /link")
    return None


@router.message(CommandStart())
async def start(message: Message, storage: Storage, cfg: Config) -> None:
    if not await _gate(message, storage, cfg):
        return
    # Reply-клавиатуры живут у клиента, пока их явно не убрать, и переживают смену
    # реализации бота. Оставшиеся от прежней версии кнопки шлют текст, которого этот
    # бот не понимает, — выглядит как «кнопка не работает».
    await message.answer(HELP, reply_markup=ReplyKeyboardRemove())


@router.message(Command("help"))
async def help_cmd(message: Message, storage: Storage, cfg: Config) -> None:
    if not await _gate(message, storage, cfg):
        return
    await message.answer(HELP)


@router.message(Command("attribution"))
async def attribution(message: Message) -> None:
    await message.answer(ATTRIBUTION)


@router.message(Command("allow"))
async def allow(
    message: Message, command: CommandObject, storage: Storage, cfg: Config
) -> None:
    if message.from_user.id != cfg.owner_id:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: <code>/allow 123456789</code>")
        return
    invited = int(command.args.strip())
    await storage.allow(invited)
    await message.answer(f"Доступ открыт для <code>{invited}</code>.")


@router.message(Command("link"))
async def link(
    message: Message, state: FSMContext, storage: Storage, cfg: Config, fs: FatSecretClient
) -> None:
    if not await _gate(message, storage, cfg):
        return
    try:
        token, secret, url = await fs.request_token()
    except FatSecretError as exc:
        await message.answer(f"FatSecret не выдал токен: {exc.message}")
        return

    await state.set_state(Link.waiting_pin)
    await state.update_data(token=token, secret=secret)
    await message.answer(
        "1. Открой ссылку и разреши доступ:\n"
        f"{url}\n\n"
        "2. FatSecret покажет PIN — пришли его сюда ответным сообщением."
    )


@router.message(Link.waiting_pin)
async def link_pin(
    message: Message, state: FSMContext, storage: Storage, cfg: Config, fs: FatSecretClient
) -> None:
    data = await state.get_data()
    pin = (message.text or "").strip()
    try:
        token, secret = await fs.access_token(data["token"], data["secret"], pin)
    except FatSecretError as exc:
        await message.answer(f"{exc.message}\nПришли PIN ещё раз или начни заново: /link")
        return

    await storage.save_link(message.from_user.id, token, secret)
    await state.set_state(Link.waiting_tz)
    await message.answer(
        "Аккаунт привязан.\n\nТеперь часовой пояс — от него зависит, в какой день "
        f"попадёт еда. Пришли название вида <code>{cfg.default_tz}</code> "
        "или напиши «по умолчанию»."
    )


@router.message(Link.waiting_tz)
@router.message(Command("tz"))
async def set_tz(message: Message, state: FSMContext, storage: Storage, cfg: Config) -> None:
    text = (message.text or "").strip()
    if text.startswith("/tz"):
        _, _, text = text.partition(" ")
        text = text.strip()
        if not text:
            await state.set_state(Link.waiting_tz)
            await message.answer(
                f"Пришли часовой пояс, например <code>{cfg.default_tz}</code>."
            )
            return

    tz = cfg.default_tz if text.lower() in {"по умолчанию", "default"} else text
    if tz not in available_timezones():
        await message.answer(
            "Не знаю такого пояса. Нужно имя из базы IANA, например "
            f"<code>{cfg.default_tz}</code> или <code>Europe/Berlin</code>."
        )
        return

    await storage.set_tz(message.from_user.id, tz)
    await state.clear()
    await message.answer(f"Часовой пояс: <b>{tz}</b>. Можно писать еду.")


@router.message(Command("undo"))
async def undo(message: Message, storage: Storage, cfg: Config, fs: FatSecretClient) -> None:
    if not await _gate(message, storage, cfg):
        return
    user = await _linked(message, storage)
    if not user:
        return

    last = await storage.last_batch(user.user_id)
    if not last:
        await message.answer("Нечего отменять — записей от бота ещё не было.")
        return

    batch_id, entry_ids = last
    removed, failed = 0, []
    for entry_id in entry_ids:
        try:
            await fs.delete_entry(user.token, user.token_secret, entry_id)
            removed += 1
        except FatSecretError as exc:
            failed.append(exc.message)

    await storage.delete_batch(batch_id)
    text = f"Удалил записей: {removed}."
    if failed:
        text += "\nНе удалось: " + "; ".join(failed[:3])
    await message.answer(text)


@router.message(Edit.waiting_amount)
async def amount_reply(
    message: Message, state: FSMContext, storage: Storage, fs: FatSecretClient
) -> None:
    log.info("получено количество: %r", message.text)
    raw = (message.text or "").replace(",", ".").strip()
    match = re.search(r"\d+(\.\d+)?", raw)
    if not match:
        await message.answer("Нужно число, например <code>150</code>.")
        return

    data = await state.get_data()
    draft_id, index = data["draft_id"], data["index"]
    draft = await storage.get_draft(draft_id)
    if not draft:
        await state.clear()
        await message.answer("Черновик уже неактуален — пришли еду заново.")
        return

    await set_amount(fs, draft["items"][index], float(match.group(0)))
    await storage.update_draft(draft_id, draft)
    await state.clear()
    await message.answer(
        ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
    )


@router.message(F.text.regexp(BARCODE))
async def barcode(
    message: Message,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
) -> None:
    if not await _gate(message, storage, cfg):
        return
    user = await _linked(message, storage)
    if not user:
        return
    await _by_barcode(message, (message.text or "").strip(), state, storage, cfg, fs, user)


async def _by_barcode(
    message: Message,
    code: str,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    user,
) -> None:
    note = await message.answer("Ищу по штрих-коду…")

    # Сначала своя Связка: она появляется, когда продукта не было в базе и мы его
    # создали, — второй раз тот же товар не должен требовать ни фото, ни ввода.
    food_id = await storage.bound_food(user.user_id, code)
    if food_id:
        log.info("штрих-код %s → свой продукт %s", code, food_id)
    else:
        try:
            food_id = await fs.food_id_by_barcode(code)
        except FatSecretError as exc:
            await note.edit_text(f"FatSecret не ответил: {exc.message}")
            return

    if not food_id:
        await state.set_state(Barcode.waiting_label)
        await state.update_data(barcode=code)
        await note.edit_text(BARCODE_UNKNOWN)
        return

    draft = await draft_from_food(fs, food_id, user.tz or cfg.default_tz)
    draft_id = await storage.save_draft(user.user_id, draft)
    await note.edit_text(ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft))


@router.message(Barcode.waiting_label, F.photo)
async def label_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
) -> None:
    data = await state.get_data()
    await state.clear()
    await _photo_flow(message, bot, state, storage, cfg, fs, llm, barcode=data.get("barcode"))


@router.message(F.photo)
async def photo(
    message: Message,
    bot: Bot,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
) -> None:
    if not await _gate(message, storage, cfg):
        return
    await _photo_flow(message, bot, state, storage, cfg, fs, llm)


async def _photo_flow(
    message: Message,
    bot: Bot,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    barcode: str | None = None,
) -> None:
    user = await _linked(message, storage)
    if not user:
        return

    note = await message.answer("Смотрю фото…")
    buffer = BytesIO()
    await bot.download(message.photo[-1], destination=buffer)

    # Сначала декодер: если на фото есть штрих-код, продукт определяется точно, и
    # звать LLM незачем — это лишние секунды, лишний запрос из лимита и лишний риск
    # ошибиться в цифрах.
    scanned = barcodes.decode(buffer.getvalue())
    if scanned:
        await note.delete()
        await _by_barcode(message, scanned, state, storage, cfg, fs, user)
        return

    try:
        recognition = await llm.recognize_photo(buffer.getvalue(), message.caption)
    except LLMError as exc:
        log.warning("распознавание фото не удалось: %s", exc)
        await note.edit_text(_llm_failure_text(exc, "фото"))
        return

    await _present(note, recognition, user, storage, fs, cfg, barcode=barcode)


@router.message(F.text)
async def text(
    message: Message,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
) -> None:
    if not await _gate(message, storage, cfg):
        return
    user = await _linked(message, storage)
    if not user:
        return

    note = await message.answer("Разбираю…")
    try:
        recognition = await llm.recognize_text(message.text or "")
    except LLMError as exc:
        log.warning("разбор текста не удался: %s", exc)
        await note.edit_text(_llm_failure_text(exc, "текст"))
        return

    await _present(note, recognition, user, storage, fs, cfg)


async def _present(
    note: Message, recognition, user, storage: Storage, fs, cfg, barcode: str | None = None
) -> None:
    """Между «модель ответила» и «показал черновик» несколько сетевых шагов.

    Каждый логируется: без этого зависший или молча упавший запрос выглядит для
    пользователя как «бот написал „Смотрю фото…“ и пропал», а в логах не остаётся
    ничего, по чему можно понять, где именно он застрял.
    """
    log.info(
        "распознано позиций: %d (%s)",
        len(recognition.items),
        ", ".join(item.query_en for item in recognition.items)[:120],
    )

    recent = await fs.recently_eaten(user.token, user.token_secret)
    log.info("недавно съеденных для ранжирования: %d", len(recent))

    draft = await build_draft(fs, recognition, user.tz or cfg.default_tz, recent, barcode)
    found = sum(1 for item in draft["items"] if item.get("food_id"))
    log.info("кандидаты найдены для %d из %d позиций", found, len(draft["items"]))

    draft_id = await storage.save_draft(user.user_id, draft)
    await note.edit_text(
        ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
    )
    log.info("черновик %d показан", draft_id)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Молчание — худший из возможных ответов: человек не знает, ждать ему или нет.

    Любое необработанное исключение попадает сюда, пишется в лог со стеком и
    превращается в честное сообщение пользователю.
    """
    log.exception("необработанная ошибка: %s", event.exception)

    message = getattr(event.update, "message", None) or getattr(
        getattr(event.update, "callback_query", None), "message", None
    )
    if message:
        with suppress(Exception):
            await message.answer(
                "Что-то сломалось на моей стороне — я записал это в лог. "
                "Попробуй ещё раз; если повторится, скажи владельцу бота."
            )
    return True


@router.callback_query()
async def callbacks(
    call: CallbackQuery, state: FSMContext, storage: Storage, fs: FatSecretClient
) -> None:
    draft_id, action, arg = ui.parse_cb(call.data or "")
    log.info("кнопка %r arg=%r черновик=%s", action, arg, draft_id)

    draft = await storage.get_draft(draft_id)
    if draft is None:
        await call.answer("Черновик уже неактуален", show_alert=True)
        return

    if action == ui.CANCEL:
        await storage.delete_draft(draft_id)
        await call.message.edit_text("Отменил, в дневник ничего не пошло.")
        await call.answer()
        return

    if action == ui.WRITE:
        user = await storage.get_user(call.from_user.id)
        if not user or not user.is_linked:
            await call.answer("Сначала /link", show_alert=True)
            return
        report = await write_draft(fs, draft, user.token, user.token_secret)
        await storage.update_draft(draft_id, draft)
        if report.entry_ids:
            await storage.save_batch(user.user_id, report.entry_ids)
        if report.token_invalid:
            await storage.invalidate_link(user.user_id)
        if not report.failed:
            await storage.delete_draft(draft_id)
            await call.message.edit_text(render_report(report))
        else:
            await call.message.edit_text(
                render_report(report), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
        await call.answer()
        return

    if action == ui.EDIT:
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.edit_keyboard(draft_id, draft)
        )
    elif action == ui.PICK_ITEM:
        index = int(arg)
        await call.message.edit_text(
            ui.render_draft(draft),
            reply_markup=ui.item_keyboard(draft_id, index, draft["items"][index]),
        )
    elif action == ui.PICK_CANDIDATE:
        index, position = (int(part) for part in arg.split("."))
        await apply_candidate(fs, draft["items"][index], position)
        await storage.update_draft(draft_id, draft)
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )
    elif action == ui.CREATE_FOOD:
        user = await storage.get_user(call.from_user.id)
        if not user or not user.is_linked:
            await call.answer("Сначала /link", show_alert=True)
            return
        index = int(arg)
        await call.answer("Создаю продукт…")
        try:
            food_id = await create_own_food(
                fs, draft["items"][index], user.token, user.token_secret
            )
        except FatSecretError as exc:
            await call.message.answer(f"Не удалось создать продукт: {exc.message}")
            return

        # Связка живёт у нас, а не в FatSecret: следующее сканирование этого кода
        # найдёт продукт сразу, без фото и без создания дубля.
        if food_id and draft.get("barcode"):
            await storage.bind_barcode(user.user_id, draft["barcode"], food_id)
            log.info("связал штрих-код %s с продуктом %s", draft["barcode"], food_id)

        await storage.update_draft(draft_id, draft)
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )
        return

    elif action == ui.ASK_GRAMS:
        await state.set_state(Edit.waiting_amount)
        await state.update_data(draft_id=draft_id, index=int(arg))
        log.info("жду количество для позиции %s черновика %s", arg, draft_id)
        await call.message.answer("Пришли количество числом — граммы или штуки.")
    elif action == ui.PICK_MEAL:
        if not arg:
            await call.message.edit_reply_markup(reply_markup=ui.meal_keyboard(draft_id))
        else:
            draft["meal"] = arg
            await storage.update_draft(draft_id, draft)
            await call.message.edit_text(
                ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
    elif action == ui.PICK_DATE:
        if not arg:
            await call.message.edit_reply_markup(reply_markup=ui.date_keyboard(draft_id))
        else:
            shift_day(draft, arg)
            await storage.update_draft(draft_id, draft)
            await call.message.edit_text(
                ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
    elif action == ui.BACK:
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )

    await call.answer()
