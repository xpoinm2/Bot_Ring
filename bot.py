# bot.py
# aiogram>=3.4,<4  |  FFmpeg должен быть в PATH
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Set, Dict, Any

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
)

# ---------- ЛОГИ ----------
LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("video_circle_bot")

# ---------- КОНСТАНТЫ И ПУТИ ----------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
ACCESS_FILE = DATA_DIR / "access.json"
SAVE_LOCK = asyncio.Lock()

# Админы задаются через переменную окружения BOT_ADMINS: "12345,67890"
def _parse_admins(env: Optional[str]) -> Set[int]:
    ids: Set[int] = set()
    if not env:
        return ids
    for part in env.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            pass
    return ids

ADMINS: Set[int] = _parse_admins(os.environ.get("BOT_ADMINS"))

# ---------- КЛАВИАТУРА ----------
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎥 Конвертировать видео")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

# ---------- ХРАНИЛИЩЕ ДОСТУПОВ ----------
def _empty_access() -> Dict[str, Any]:
    return {"usernames": [], "ids": []}

def _load_access() -> Dict[str, Any]:
    if not ACCESS_FILE.exists():
        return _empty_access()
    try:
        data = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_access()
        # нормализуем
        data.setdefault("usernames", [])
        data.setdefault("ids", [])
        # нижний регистр для @
        data["usernames"] = sorted({str(u).lstrip("@").lower() for u in data["usernames"] if u})
        # только int для ids
        norm_ids = set()
        for i in data["ids"]:
            try:
                norm_ids.add(int(i))
            except Exception:
                pass
        data["ids"] = sorted(norm_ids)
        return data
    except Exception:
        return _empty_access()

async def _save_access(data: Dict[str, Any]) -> None:
    async with SAVE_LOCK:
        ACCESS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _user_key(m: Message) -> Dict[str, Optional[str | int]]:
    username = (m.from_user.username or "").strip()
    uname_norm = username.lstrip("@").lower() if username else None
    uid = m.from_user.id
    return {"username": uname_norm, "id": uid}

def _is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def _has_access(m: Message, access: Dict[str, Any]) -> bool:
    u = _user_key(m)
    if _is_admin(u["id"]):  # админам всегда можно
        return True
    # доступ по username
    if u["username"] and u["username"] in set(access.get("usernames", [])):
        return True
    # доступ по id (на случай пользователей без username)
    if u["id"] in set(access.get("ids", [])):
        return True
    return False

async def _ensure_access_or_explain(m: Message) -> bool:
    access = _load_access()
    if _has_access(m, access):
        return True
    # вежливое сообщение
    uname = f"@{m.from_user.username}" if m.from_user.username else None
    who = uname or f"id:{m.from_user.id}"
    await m.answer(
        "⛔ Нет доступа.\n"
        "Попросите администратора выдать доступ.\n\n"
        f"Ваш идентификатор: {who}"
    )
    return False

# ---------- FFmpeg КОНВЕРТАЦИЯ ----------
async def ffmpeg_convert(src: Path, dst: Path) -> None:
    # Без кавычек и min(): уменьшаем до 480 по большей стороне, дополняем паддингом до квадрата
    args = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", "scale=480:480:force_original_aspect_ratio=decrease,pad=480:480:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-t", "59",  # можно увеличить/убрать при желании
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast",
        "-profile:v", "main", "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "1",
        "-movflags", "+faststart",
        str(dst),
    ]
    log.info("FFmpeg cmd: %s", " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        err_txt = err.decode("utf-8", "ignore")
        raise RuntimeError(err_txt or "ffmpeg failed")

# ---------- РАБОТА С МЕДИА ----------
async def download_media(bot: Bot, file_id: str, dst: Path) -> None:
    f = await bot.get_file(file_id)
    log.info("Downloading: %s -> %s", f.file_path, dst)
    await bot.download_file(f.file_path, dst)

async def handle_video(message: Message, file_id: str, original_name: Optional[str]) -> None:
    if not await _ensure_access_or_explain(message):
        return

    bot: Bot = message.bot
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO_NOTE)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        suffix = Path(original_name).suffix if original_name else ".mp4"
        src = tmpdir / f"src{suffix}"
        out = tmpdir / "out.mp4"

        try:
            await download_media(bot, file_id, src)
            await ffmpeg_convert(src, out)
            await message.answer_video_note(video_note=FSInputFile(out), length=480)
        except Exception as e:
            log.exception("Failed to process video")
            await message.answer(f"⚠️ Ошибка: {e}")

# ---------- КОМАНДЫ АДМИНИСТРАТОРА ----------
def _require_admin(func):
    async def wrapper(m: Message, *args, **kwargs):
        if not _is_admin(m.from_user.id):
            await m.answer("⛔ Эта команда доступна только администраторам.")
            return
        return await func(m, *args, **kwargs)
    return wrapper

@_require_admin
async def cmd_grant(m: Message):
    """
    /grant @username  — выдать доступ по тегу
    /grant_id 123456  — выдать доступ по ID (если у пользователя нет username)
    """
    text = m.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование:\n/grant @username")
        return
    username = parts[1].strip().lstrip("@").lower()
    if not username:
        await m.answer("Укажи тег: /grant @username")
        return
    data = _load_access()
    uset = set(data.get("usernames", []))
    uset.add(username)
    data["usernames"] = sorted(uset)
    await _save_access(data)
    await m.answer(f"✅ Доступ выдан: @{username}")

@_require_admin
async def cmd_revoke(m: Message):
    text = m.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование:\n/revoke @username")
        return
    username = parts[1].strip().lstrip("@").lower()
    data = _load_access()
    uset = set(data.get("usernames", []))
    if username in uset:
        uset.remove(username)
        data["usernames"] = sorted(uset)
        await _save_access(data)
        await m.answer(f"✅ Доступ отозван: @{username}")
    else:
        await m.answer(f"Пользователь @{username} не найден в списке доступа.")

@_require_admin
async def cmd_grant_id(m: Message):
    text = m.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование:\n/grant_id 123456789")
        return
    try:
        uid = int(parts[1].strip())
    except ValueError:
        await m.answer("ID должен быть числом: /grant_id 123456789")
        return
    data = _load_access()
    ids = set(int(x) for x in data.get("ids", []))
    ids.add(uid)
    data["ids"] = sorted(ids)
    await _save_access(data)
    await m.answer(f"✅ Доступ по ID выдан: {uid}")

@_require_admin
async def cmd_revoke_id(m: Message):
    text = m.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование:\n/revoke_id 123456789")
        return
    try:
        uid = int(parts[1].strip())
    except ValueError:
        await m.answer("ID должен быть числом: /revoke_id 123456789")
        return
    data = _load_access()
    ids = set(int(x) for x in data.get("ids", []))
    if uid in ids:
        ids.remove(uid)
        data["ids"] = sorted(ids)
        await _save_access(data)
        await m.answer(f"✅ Доступ по ID отозван: {uid}")
    else:
        await m.answer(f"ID {uid} не найден в списке доступа.")

@_require_admin
async def cmd_list_access(m: Message):
    data = _load_access()
    users = data.get("usernames", [])
    ids = data.get("ids", [])
    txt = "📜 Список доступа:\n"
    if users:
        txt += "\nТеги:\n" + "\n".join(f"• @{u}" for u in users)
    else:
        txt += "\nТеги: —"
    if ids:
        txt += "\n\nID:\n" + "\n".join(f"• {i}" for i in ids)
    else:
        txt += "\n\nID: —"
    await m.answer(txt)

async def cmd_whoami(m: Message):
    u = m.from_user
    uname = f"@{u.username}" if u.username else "(нет username)"
    await m.answer(f"Вы: {uname}\nID: {u.id}\nАдмин: {'да' if _is_admin(u.id) else 'нет'}")

# ---------- MAIN ----------
async def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN (переменная окружения).")
    bot = Bot(token)
    dp = Dispatcher()

    # Базовые команды
    @dp.message(Command("start", "help"))
    async def start(m: Message):
        await m.answer(
            "Отправь видео — сделаю кружок Telegram.\n"
            "Доступ выдают админы. Команды админа: /grant, /revoke, /grant_id, /revoke_id, /list_access",
            reply_markup=MAIN_KB
        )

    @dp.message(Command("whoami"))
    async def who(m: Message):
        await cmd_whoami(m)

    # Админ-команды
    @dp.message(Command("grant"))
    async def _grant(m: Message):
        await cmd_grant(m)

    @dp.message(Command("revoke"))
    async def _revoke(m: Message):
        await cmd_revoke(m)

    @dp.message(Command("grant_id"))
    async def _grant_id(m: Message):
        await cmd_grant_id(m)

    @dp.message(Command("revoke_id"))
    async def _revoke_id(m: Message):
        await cmd_revoke_id(m)

    @dp.message(Command("list_access"))
    async def _list(m: Message):
        await cmd_list_access(m)

    # Кнопки
    @dp.message(F.text == "🎥 Конвертировать видео")
    async def ask(m: Message):
        if await _ensure_access_or_explain(m):
            await m.answer("Жду видео или пересланное видео.", reply_markup=MAIN_KB)

    @dp.message(F.text == "ℹ️ Помощь")
    async def help_(m: Message):
        await m.answer("Установи ffmpeg. Отправь видео — получишь видео-кружок. Доступ выдаёт админ.", reply_markup=MAIN_KB)

    # Медиа
    @dp.message(F.video)
    async def vid(m: Message):
        await handle_video(m, m.video.file_id, m.video.file_name)

    @dp.message(F.document & F.document.mime_type.startswith("video/"))
    async def doc(m: Message):
        await handle_video(m, m.document.file_id, m.document.file_name)

    @dp.message(F.animation)
    async def anim(m: Message):
        await handle_video(m, m.animation.file_id, m.animation.file_name)

    log.info("Starting polling…")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
