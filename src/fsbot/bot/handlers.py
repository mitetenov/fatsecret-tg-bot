"""Команды и диалог. Доступ — только по приглашению (решение 8)."""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from html import escape
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
    apply_portion_choice,
    build_draft,
    draft_from_web,
    create_own_food,
    draft_from_food,
    render_report,
    refresh_confidence,
    set_amount,
    shift_day,
    write_draft,
)
from fsbot.config import Config
from fsbot.domain import barcodes, naming
from fsbot.fatsecret.client import FatSecretClient, FatSecretError, FatSecretUnknownOutcome
from fsbot.foodfacts import OpenFoodFacts
from fsbot.llm.openrouter import LLMError, OpenRouter
from fsbot.storage import Storage

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

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

Пришли фото упаковки с таблицей пищевой ценности — прочитаю КБЖУ, предложу создать
продукт в твоём аккаунте и запомню этот код: в следующий раз хватит сканирования."""


class Link(StatesGroup):
    waiting_pin = State()
    waiting_tz = State()


class Edit(StatesGroup):
    waiting_amount = State()
    waiting_portions = State()


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
        await message.answer(f"FatSecret не выдал токен: {escape(str(exc.message))}")
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
        await message.answer(f"{escape(str(exc.message))}\nПришли PIN ещё раз или начни заново: /link")
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
    if not await _gate(message, storage, cfg):
        return
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

    last = await storage.claim_last_batch(user.user_id)
    if not last:
        await message.answer("Нечего отменять или последняя запись сейчас обрабатывается.")
        return

    batch_id, entry_ids = last
    removed, failed = [], []
    try:
        for entry_id in entry_ids:
            try:
                await fs.delete_entry(user.token, user.token_secret, entry_id)
                await storage.record_undo_success(batch_id, user.user_id, entry_id)
                removed.append(entry_id)
            except (FatSecretError, FatSecretUnknownOutcome) as exc:
                failed.append(exc.message if isinstance(exc, FatSecretError) else str(exc))
    finally:
        await storage.release_batch(batch_id, user.user_id)
    answer = f"Удалил записей: {len(removed)}."
    if failed:
        answer += "\nНе удалось: " + "; ".join(escape(str(error)) for error in failed[:3])
    await message.answer(answer)


@router.message(Edit.waiting_amount)
async def amount_reply(
    message: Message, state: FSMContext, storage: Storage, fs: FatSecretClient
) -> None:
    log.info("получено количество: %r", message.text)
    amount = _positive_number(message.text or "")
    if amount is None:
        await message.answer("Нужно положительное число, например <code>150</code>.")
        return

    data = await state.get_data()
    draft_id, index = data["draft_id"], data["index"]
    draft = await storage.get_draft(draft_id, message.from_user.id)
    if not draft:
        await state.clear()
        await message.answer("Черновик уже неактуален — пришли еду заново.")
        return

    await state.clear()
    await _apply_amount(message, draft_id, draft, index, amount, storage, fs)


def _positive_number(raw: str) -> float | None:
    match = re.fullmatch(
        r"\s*(\d+(?:[.,]\d+)?)\s*(?:г|гр|g|мл|ml|шт|pieces?)?\s*",
        raw,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 0 < value <= 100000 else None


@router.message(Edit.waiting_portions)
async def portions_reply(
    message: Message, state: FSMContext, storage: Storage, fs: FatSecretClient
) -> None:
    count = _positive_number(message.text or "")
    if count is None:
        await message.answer("Пришли количество порций числом, например <code>1,5</code>.")
        return
    data = await state.get_data()
    draft_id, index = data["draft_id"], data["index"]
    draft = await storage.get_draft(draft_id, message.from_user.id)
    if not draft:
        await state.clear()
        await message.answer("Черновик уже неактуален — пришли еду заново.")
        return
    await state.clear()
    claimed = await storage.claim_draft(draft_id, message.from_user.id)
    if claimed is None:
        await message.answer("Черновик сейчас обрабатывается или уже закрыт.")
        return
    try:
        item = claimed["items"][index]
        if item.get("status") in {"written", "undone", "writing", "unknown", "creating", "create_unknown"}:
            await message.answer("Эту позицию нельзя менять сейчас.")
            return
        await apply_portion_choice(fs, item, data["serving_id"], count)
        await _save_and_show_card(message, draft_id, claimed, storage)
    finally:
        await storage.release_draft(draft_id, message.from_user.id)


async def _apply_amount(
    message: Message,
    draft_id: int,
    draft: dict,
    index: int,
    amount: float,
    storage: Storage,
    fs: FatSecretClient,
) -> None:
    """Пересчитать пункт и обновить ту же карточку.

    Новым сообщением отвечать нельзя: прежняя карточка остаётся в чате с живыми
    кнопками и старым количеством — человек видит «бот всё равно предлагает 360 г»
    и жмёт «Записать» на устаревшем варианте.
    """
    claimed = await storage.claim_draft(draft_id, message.from_user.id)
    if claimed is None:
        await message.answer("Черновик сейчас обрабатывается или уже закрыт.")
        return
    try:
        item = claimed["items"][index]
        if item.get("status") in {"written", "undone", "writing", "unknown", "creating", "create_unknown"}:
            await message.answer("Эту позицию нельзя менять сейчас.")
            return
        await set_amount(fs, item, amount)
        await _save_and_show_card(message, draft_id, claimed, storage)
    finally:
        await storage.release_draft(draft_id, message.from_user.id)


async def _save_and_show_card(
    message: Message, draft_id: int, draft: dict, storage: Storage
) -> None:
    draft.pop("review_prompted", None)
    refresh_confidence(draft)
    await storage.update_draft(draft_id, draft, message.from_user.id)
    text, markup = ui.render_draft(draft), ui.draft_keyboard(draft_id, draft)
    card = draft.get("card_message_id")
    if card:
        with suppress(Exception):
            await message.bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=card, reply_markup=markup
            )
            return
    sent = await message.answer(text, reply_markup=markup)
    draft["card_message_id"] = sent.message_id
    await storage.update_draft(draft_id, draft, message.from_user.id)


@router.message(F.text.regexp(BARCODE))
async def barcode(
    message: Message,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    off: OpenFoodFacts,
) -> None:
    if not await _gate(message, storage, cfg):
        return
    user = await _linked(message, storage)
    if not user:
        return
    await state.clear()
    await _by_barcode(
        message, (message.text or "").strip(), state, storage, cfg, fs, llm, off, user
    )


async def _lookup_product(code: str, off: OpenFoodFacts, llm: OpenRouter) -> dict | None:
    """Открытая база сначала, модель — последней.

    Open Food Facts отвечает одинаково на каждый запрос и бесплатно; веб-поиск моделью
    на том же коде срабатывал в двух прогонах из пяти, поэтому он резерв, а не основа.
    """
    code = barcodes.canonical_gtin(code)
    if code is None:
        log.info("штрих-код не прошёл проверку контрольной цифры")
        return None
    product = await off.lookup(code) or await llm.lookup_barcode(code)
    if not product:
        return None

    if not naming.is_readable(product["name"]):
        log.info("название %r нечитаемо — перевожу", product["name"])
        translated = await llm.translate_product(product["name"], product.get("brand", ""))
        if translated:
            product["name"], product["brand"] = translated
        else:
            # Перевести не вышло: числа верные, но подписывать ими нечитаемую строку
            # нельзя — пусть человек назовёт продукт сам, прислав фото этикетки.
            log.info("перевод не удался, отдаю продукт без названия")
            return None
    return product


async def _food_by_barcode(code: str, storage: Storage, fs: FatSecretClient, user) -> str | None:
    """Своя Связка важнее базы: если мы уже создавали этот продукт, второе сканирование
    не должно ни спрашивать фото, ни плодить дубль."""
    food_id = await storage.bound_food(user.user_id, code)
    if food_id:
        log.info("штрих-код %s → свой продукт %s", code, food_id)
        return food_id
    gtin13 = barcodes.fatsecret_gtin13(code)
    if gtin13 is None:
        return None
    try:
        return await fs.food_id_by_barcode(gtin13)
    except FatSecretError as exc:
        if exc.code == 211:
            return None
        raise


async def _show_food(note: Message, food_id: str, user, storage: Storage, cfg, fs) -> None:
    draft = await draft_from_food(fs, food_id, user.tz or cfg.default_tz)
    draft_id = await storage.save_draft(user.user_id, draft)
    draft["card_message_id"] = note.message_id
    await storage.update_draft(draft_id, draft, user.user_id)
    await note.edit_text(ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft))


async def _by_barcode(
    message: Message,
    code: str,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    off: OpenFoodFacts,
    user,
) -> None:
    if not barcodes.valid_gtin(code):
        await message.answer("Штрих-код неверной длины или с неправильной контрольной цифрой.")
        return
    note = await message.answer("Ищу по штрих-коду…")

    food_id = await _food_by_barcode(code, storage, fs, user)
    if food_id:
        await _show_food(note, food_id, user, storage, cfg, fs)
        return

    await note.edit_text("В базе FatSecret кода нет — ищу товар по коду…")
    product = await _lookup_product(code, off, llm)
    if product:
        draft = await draft_from_web(fs, product, user.tz or cfg.default_tz, code)
        draft_id = await storage.save_draft(user.user_id, draft)
        draft["card_message_id"] = note.message_id
        await storage.update_draft(draft_id, draft, user.user_id)
        await note.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )
        return

    await state.set_state(Barcode.waiting_label)
    await state.update_data(barcode=code)
    await note.edit_text(BARCODE_UNKNOWN)


@router.message(Barcode.waiting_label, F.photo)
async def label_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    off: OpenFoodFacts,
) -> None:
    data = await state.get_data()
    await state.clear()
    await _photo_flow(
        message, bot, state, storage, cfg, fs, llm, off, barcode=data.get("barcode")
    )


@router.message(F.photo)
async def photo(
    message: Message,
    bot: Bot,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    off: OpenFoodFacts,
) -> None:
    if not await _gate(message, storage, cfg):
        return
    await _photo_flow(message, bot, state, storage, cfg, fs, llm, off)


async def _photo_flow(
    message: Message,
    bot: Bot,
    state: FSMContext,
    storage: Storage,
    cfg: Config,
    fs: FatSecretClient,
    llm: OpenRouter,
    off: OpenFoodFacts,
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
        found = await _food_by_barcode(scanned, storage, fs, user)
        if found:
            await _show_food(note, found, user, storage, cfg, fs)
            return
        # Кода нет в индексе FatSecret. Данные по нему сверим с поиском по названию
        # прежде, чем предлагать создать свой продукт.
        log.info("штрих-код %s не найден в базе — ищу товар по коду", scanned)
        await note.edit_text("Кода нет в базе FatSecret — ищу товар по коду…")
        product = await _lookup_product(scanned, off, llm)
        if product:
            draft = await draft_from_web(fs, product, user.tz or cfg.default_tz, scanned)
            draft_id = await storage.save_draft(user.user_id, draft)
            draft["card_message_id"] = note.message_id
            await storage.update_draft(draft_id, draft, user.user_id)
            await note.edit_text(
                ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
            return
        # Ни база, ни веб не знают товар — остаётся прочитать этикетку с фото.
        await note.edit_text("Не нашёл товар по коду — читаю упаковку…")
        barcode = scanned

    try:
        recognition = await llm.recognize_photo(
            buffer.getvalue(), message.caption, barcode=barcode
        )
    except LLMError as exc:
        log.warning("распознавание фото не удалось: %s", exc)
        await note.edit_text(_llm_failure_text(exc, "фото"))
        return

    await _present(note, recognition, user, storage, fs, cfg, barcode=barcode)


AMOUNT_ONLY = re.compile(r"^\d{1,4}([.,]\d+)?\s*(г|гр|g|мл|ml)?$", re.IGNORECASE)


@router.message(F.text.regexp(AMOUNT_ONLY))
async def bare_amount(
    message: Message, state: FSMContext, storage: Storage, cfg: Config,
    fs: FatSecretClient, llm: OpenRouter
) -> None:
    """«450» после карточки — это правка количества, а не новая еда.

    Нажимать «Изменить → Указать количество» ради одного числа никто не хочет, а
    отправлять его в распознавание бессмысленно: продукта в нём нет.
    """
    if not await _gate(message, storage, cfg):
        return
    user = await _linked(message, storage)
    if not user:
        return
    if await state.get_state() == Barcode.waiting_label:
        await state.clear()

    latest = await storage.last_draft(user.user_id)
    if not latest or len(latest[1].get("items", [])) != 1:
        # Карточки нет или пунктов несколько — непонятно, к чему относить число.
        await text(message, state, storage, cfg, fs, llm)
        return

    draft_id, draft = latest
    amount = _positive_number(message.text or "")
    if amount is None:
        await message.answer("Количество должно быть положительным.")
        return
    log.info("число %s применяю к черновику %s без кнопки", amount, draft_id)
    await _apply_amount(message, draft_id, draft, 0, amount, storage, fs)


@router.message(F.text)
async def text(
    message: Message,
    state: FSMContext,
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
    if await state.get_state() == Barcode.waiting_label:
        await state.clear()

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

    try:
        recent = await fs.recently_eaten(user.token, user.token_secret)
    except FatSecretUnknownOutcome:
        recent = []
    log.info("недавно съеденных для ранжирования: %d", len(recent))

    draft = await build_draft(fs, recognition, user.tz or cfg.default_tz, recent, barcode)
    found = sum(1 for item in draft["items"] if item.get("food_id"))
    log.info("кандидаты найдены для %d из %d позиций", found, len(draft["items"]))

    draft_id = await storage.save_draft(user.user_id, draft)
    draft["card_message_id"] = note.message_id
    await storage.update_draft(draft_id, draft, user.user_id)
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
    call: CallbackQuery,
    state: FSMContext,
    storage: Storage,
    fs: FatSecretClient,
    cfg: Config,
) -> None:
    try:
        draft_id, action, arg = ui.parse_cb(call.data or "")
    except (ValueError, TypeError):
        await call.answer("Неизвестная кнопка", show_alert=True)
        return
    log.info("кнопка %r arg=%r черновик=%s", action, arg, draft_id)
    user_id = call.from_user.id
    draft = await storage.claim_draft(draft_id, user_id)
    if draft is None:
        await call.answer("Черновик уже неактуален или обрабатывается", show_alert=True)
        return
    try:
        await _handle_callback(
            call, state, storage, fs, cfg, draft_id, action, arg, draft
        )
    finally:
        await storage.release_draft(draft_id, user_id)


async def _handle_callback(
    call: CallbackQuery, state: FSMContext, storage: Storage, fs: FatSecretClient,
    cfg: Config, draft_id: int, action: str, arg: str, draft: dict,
) -> None:
    user_id = call.from_user.id

    if action == ui.CANCEL:
        written = sum(item.get("status") == "written" for item in draft["items"])
        unknown = any(item.get("status") in {"writing", "unknown", "creating", "create_unknown"}
                      for item in draft["items"])
        await storage.delete_draft(draft_id, user_id)
        if written or unknown:
            notice = f"Черновик закрыт. Уже записано позиций: {written}."
            if written:
                notice += " Их можно удалить командой /undo."
            if unknown:
                notice += " Исход ещё одного запроса неизвестен — проверь FatSecret."
        else:
            notice = "Отменил, в дневник ничего не пошло."
        await call.message.edit_text(notice)
        await call.answer()
        return

    if action == ui.REVIEW:
        draft["review_prompted"] = True
        await storage.update_draft(draft_id, draft, user_id)
        await call.message.edit_reply_markup(reply_markup=ui.review_keyboard(draft_id))
        await call.answer(
            "Проверь продукт, количество и КБЖУ. Повторное нажатие выполнит запись.",
            show_alert=True,
        )
        return

    if action == ui.WRITE:
        if draft.get("needs_review") and not draft.get("review_prompted"):
            draft["review_prompted"] = True
            await storage.update_draft(draft_id, draft, user_id)
            await call.message.edit_reply_markup(reply_markup=ui.review_keyboard(draft_id))
            await call.answer("Нужна дополнительная проверка", show_alert=True)
            return
        user = await storage.get_user(call.from_user.id)
        if not user or not user.is_linked:
            await call.answer("Сначала /link", show_alert=True)
            return
        async def persist() -> None:
            await storage.update_draft(draft_id, draft, user_id)

        async def record(entry_id: str) -> None:
            await storage.record_write_success(draft_id, user_id, draft, entry_id)

        report = await write_draft(
            fs, draft, user.token, user.token_secret,
            persist=persist, record_success=record,
        )
        if report.token_invalid:
            await storage.invalidate_link(user.user_id)
        if not report.failed:
            await storage.delete_draft(draft_id, user_id)
            await call.message.edit_text(render_report(report) if report.written else "Все позиции записаны.")
        else:
            await call.message.edit_text(
                render_report(report), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
        await call.answer()
        return

    if action == ui.EDIT:
        draft.pop("review_prompted", None)
        await storage.update_draft(draft_id, draft, user_id)
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.edit_keyboard(draft_id, draft)
        )
    elif action == ui.PICK_ITEM:
        index = int(arg)
        if draft["items"][index].get("status") in {"written", "undone", "writing", "unknown", "creating", "create_unknown"}:
            await call.answer("Эту позицию нельзя менять", show_alert=True)
            return
        await call.message.edit_text(
            ui.render_draft(draft),
            reply_markup=ui.item_keyboard(draft_id, index, draft["items"][index]),
        )
    elif action == ui.PICK_CANDIDATE:
        index, position = (int(part) for part in arg.split("."))
        if draft["items"][index].get("status") in {"written", "undone", "writing", "unknown", "creating", "create_unknown"}:
            await call.answer("Эту позицию нельзя менять сейчас", show_alert=True)
            return
        draft["items"][index].pop("manual_serving_id", None)
        await apply_candidate(fs, draft["items"][index], position)
        draft.pop("review_prompted", None)
        refresh_confidence(draft)
        await storage.update_draft(draft_id, draft, user_id)
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )
    elif action == ui.PICK_SERVING:
        index_str, serving_id = arg.split(".", 1)
        index = int(index_str)
        item = draft["items"][index]
        if not item.get("needs_portion") or serving_id not in {
            serving["serving_id"] for serving in item.get("available_servings") or []
        }:
            await call.answer("Порция уже неактуальна", show_alert=True)
            return
        await state.set_state(Edit.waiting_portions)
        await state.update_data(draft_id=draft_id, index=index, serving_id=serving_id)
        await call.message.answer("Сколько таких порций съел? Пришли число, например 1,5.")
    elif action == ui.CREATE_FOOD:
        user = await storage.get_user(user_id)
        if not user or not user.is_linked:
            await call.answer("Сначала /link", show_alert=True)
            return
        index = int(arg)
        item = draft["items"][index]
        if not item.get("creatable") or item.get("status", "pending") != "pending":
            await call.answer("Продукт уже создаётся или создан", show_alert=True)
            return
        await call.answer("Создаю продукт…")
        item["status"] = "creating"
        await storage.update_draft(draft_id, draft, user_id)

        async def on_created() -> None:
            await storage.update_draft(draft_id, draft, user_id)
            if draft.get("barcode") and item.get("own_food_id"):
                await storage.bind_barcode(user_id, draft["barcode"], item["own_food_id"])
                log.info("связал штрих-код %s с продуктом %s", draft["barcode"], item["own_food_id"])

        try:
            food_id = await create_own_food(
                fs, item, user.token, user.token_secret, on_created=on_created
            )
        except FatSecretError as exc:
            item["status"] = "pending"
            await storage.update_draft(draft_id, draft, user_id)
            await call.message.answer(f"Не удалось создать продукт: {escape(str(exc.message))}")
            return
        except FatSecretUnknownOutcome:
            if item.get("own_food_id"):
                # Creation returned an ID and was already persisted. Only the
                # follow-up food.get failed; choosing the candidate can retry it.
                item["status"] = "pending"
                item["error"] = "Продукт создан; не удалось загрузить порции. Выбери его ещё раз."
            else:
                item["status"] = "create_unknown"
            await storage.update_draft(draft_id, draft, user_id)
            await call.message.edit_text(ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft))
            return

        draft.pop("review_prompted", None)
        refresh_confidence(draft)
        await storage.update_draft(draft_id, draft, user_id)
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )
        return

    elif action == ui.ASK_GRAMS:
        if draft["items"][int(arg)].get("status") in {"written", "undone", "writing", "unknown", "creating", "create_unknown"}:
            await call.answer("Эту позицию нельзя менять", show_alert=True)
            return
        await state.set_state(Edit.waiting_amount)
        await state.update_data(draft_id=draft_id, index=int(arg))
        log.info("жду количество для позиции %s черновика %s", arg, draft_id)
        await call.message.answer("Пришли количество числом — граммы или штуки.")
    elif action == ui.PICK_MEAL:
        if not arg:
            await call.message.edit_reply_markup(reply_markup=ui.meal_keyboard(draft_id))
        else:
            if any(item.get("status") in {"written", "writing", "unknown"} for item in draft["items"]):
                await call.answer("Часть позиций уже записана; приём пищи теперь нельзя менять", show_alert=True)
                return
            draft["meal"] = arg
            await storage.update_draft(draft_id, draft, user_id)
            await call.message.edit_text(
                ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
    elif action == ui.PICK_DATE:
        if not arg:
            await call.message.edit_reply_markup(reply_markup=ui.date_keyboard(draft_id))
        else:
            if any(item.get("status") in {"written", "writing", "unknown"} for item in draft["items"]):
                await call.answer("Часть позиций уже записана; дату теперь нельзя менять", show_alert=True)
                return
            user = await storage.get_user(user_id)
            shift_day(draft, arg, (user.tz if user and user.tz else cfg.default_tz))
            await storage.update_draft(draft_id, draft, user_id)
            await call.message.edit_text(
                ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
            )
    elif action == ui.BACK:
        await call.message.edit_text(
            ui.render_draft(draft), reply_markup=ui.draft_keyboard(draft_id, draft)
        )

    await call.answer()
