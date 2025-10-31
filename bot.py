# bot.py
# aiogram>=3.4,<4  |  FFmpeg должен быть в PATH
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, Set, Tuple

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

# ---------- ПУТИ/ХРАНИЛИЩЕ ----------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
ACCESS_FILE = DATA_DIR / "access.json"
SAVE_LOCK = asyncio.Lock()

def _parse_ids(env: Optional[str]) -> Set[int]:
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

def _empty_access() -> Dict[str, Any]:
    # username — без @, в нижнем регистре
    return {
        "super": {"ids": [], "usernames": []},
        "admins": {"ids": [], "usernames": []},
    }

def _normalize_access(d: Dict[str, Any]) -> Dict[str, Any]:
    def norm_block(b: Dict[str, Any]) -> Dict[str, Any]:
        # ids -> ints уникальные
        ids = set()
        for v in b.get("ids", []):
            try:
                ids.add(int(v))
            except Exception:
                pass
        # usernames -> строки без @, lower
        unames = {str(u).lstrip("@").lower() for u in b.get("usernames", []) if u}
        return {"ids": sorted(ids), "usernames": sorted(unames)}

    if not isinstance(d, dict):
        return _empty_access()
    d.setdefault("super", {})
    d.setdefault("admins", {})
    d["super"] = norm_block(d["super"])
    d["admins"] = norm_block(d["admins"])
    return d

def _load_access() -> Dict[str, Any]:
    if not ACCESS_FILE.exists():
        # первичное заполнение из переменных окружения
        acc = _empty_access()
        super_ids = _parse_ids(os.environ.get("BOT_SUPER_ADMINS"))
        admin_ids = _parse_ids(os.environ.get("BOT_ADMINS"))
        acc["super"]["ids"] = sorted(super_ids)
        acc["admins"]["ids"] = sorted(admin_ids)
        return acc
    try:
        data = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = _empty_access()
    return _normalize_access(data)

async def _save_access(data: Dict[str, Any]) -> None:
    async with SAVE_LOCK:
        ACCESS_FILE.write_text(
            json.dumps(_normalize_access(data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

# ---------- РОЛИ/ПРАВА ----------
def _user_username_norm(m: Message) -> Optional[str]:
    u = m.from_user
    if not u or not u.username:
        return None
    return u.username.lstrip("@").lower()

def _in_block(m: Message, block: Dict[str, Any]) -> bool:
    uid = m.from_user.id
    uname = _user_username_norm(m)
    if uid in set(block.get("ids", [])):
        return True
    if uname and (uname in set(block.get("usernames", []))):
        return True
    return False

def is_super(m: Message, access: Dict[str, Any]) -> bool:
    return _in_block(m, access.get("super", {}))

def is_admin(m: Message, access: Dict[str, Any]) -> bool:
    # супер-админ автоматически админ
    return is_super(m, access) or _in_block(m, access.get("admins", {}))

async def _ensure_admin_access_or_explain(m: Message) -> Tuple[bool, Dict[str, Any]]:
    access = _load_access()
    if is_admin(m, access):
        return True, access
    who = f"@{m.from_user.username}" if m.from_user.username else f"id:{m.from_user.id}"
    await m.answer(
        "⛔ Доступ запрещён.\n"
        "Только админы могут пользоваться ботом.\n\n"
        f"Ваш идентификатор: {who}"
    )
    return False, access

def _require_super(func):
    async def wrapper(m: Message, *args, **kwargs):
        access = _load_access()
        if not is_super(m, access):
            await m.answer("⛔ Команда доступна только супер-админам.")
            return
        return await func(m, access, *args, **kwargs)
    return wrapper

# ---------- КЛАВИАТУРА ----------
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎥 Конвертировать видео")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)

# ---------- FFmpeg ----------
async def ffmpeg_convert(src: Path, dst: Path) -> None:
    # Без кавычек/min(): масштабируем с сохранением пропорций и дополняем паддингом до квадрата
    args = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", "scale=480:480:force_original_aspect_ratio=decrease,pad=480:480:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-t", "59",
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
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode("utf-8", "ignore") or "ffmpeg failed")

# ---------- МЕДИА ----------
async def download_media(bot: Bot, file_id: str, dst: Path) -> None:
    f = await bot.get_file(file_id)
    log.info("Downloading: %s -> %s", f.file_path, dst)
    await bot.download_file(f.file_path, dst)

async def handle_video(message: Message, file_id: str, original_name: Optional[str]) -> None:
    ok, _ = await _ensure_admin_access_or_explain(message)
    if not ok:
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

# ---------- КОМАНДЫ СУПЕР-АДМИНА ----------
def _parse_target(arg: str) -> Tuple[Optional[int], Optional[str]]:
    arg = arg.strip()
    if not arg:
        return None, None
    if arg.startswith("@"):
        return None, arg.lstrip("@").lower()
    try:
        return int(arg), None
    except ValueError:
        # может прислали просто username без @
        return None, arg.lower()

@_require_super
async def cmd_grant_admin(m: Message, access: Dict[str, Any]):
    """ /grant_admin @username | /grant_admin 123456 """
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /grant_admin @username | /grant_admin <id>")
        return
    uid, uname = _parse_target(parts[1])
    if uid is None and not uname:
        await m.answer("Укажи @username или числовой ID.")
        return

    admins = access["admins"]
    if uid is not None:
        admins["ids"] = sorted(set(admins["ids"]) | {uid})
    if uname:
        admins["usernames"] = sorted(set(admins["usernames"]) | {uname})

    await _save_access(access)
    who = f"@{uname}" if uname else uid
    await m.answer(f"✅ Выдан доступ АДМИНА: {who}")

@_require_super
async def cmd_revoke_admin(m: Message, access: Dict[str, Any]):
    """ /revoke_admin @username | /revoke_admin 123456 """
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /revoke_admin @username | /revoke_admin <id>")
        return
    uid, uname = _parse_target(parts[1])
    admins = access["admins"]
    if uid is not None:
        admins["ids"] = sorted({i for i in admins["ids"] if i != uid})
    if uname:
        admins["usernames"] = sorted({u for u in admins["usernames"] if u != uname})

    await _save_access(access)
    who = f"@{uname}" if uname else uid
    await m.answer(f"✅ Отозван доступ АДМИНА: {who}")

@_require_super
async def cmd_grant_super(m: Message, access: Dict[str, Any]):
    """ /grant_super @username | /grant_super 123456 """
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /grant_super @username | /grant_super <id>")
        return
    uid, uname = _parse_target(parts[1])

    sup = access["super"]
    if uid is not None:
        sup["ids"] = sorted(set(sup["ids"]) | {uid})
    if uname:
        sup["usernames"] = sorted(set(sup["usernames"]) | {uname})

    await _save_access(access)
    who = f"@{uname}" if uname else uid
    await m.answer(f"✅ Выдан доступ СУПЕР-АДМИНА: {who}")

@_require_super
async def cmd_revoke_super(m: Message, access: Dict[str, Any]):
    """ /revoke_super @username | /revoke_super 123456 """
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: /revoke_super @username | /revoke_super <id>")
        return
    uid, uname = _parse_target(parts[1])

    # Защита: не позволяем убрать последнего супер-админа
    def count_sup(a: Dict[str, Any]) -> int:
        return len(a["super"]["ids"]) + len(a["super"]["usernames"])

    before = count_sup(access)
    sup = access["super"]

    if uid is not None:
        sup["ids"] = sorted({i for i in sup["ids"] if i != uid})
    if uname:
        sup["usernames"] = sorted({u for u in sup["usernames"] if u != uname})

    if count_sup(access) == 0:
        await m.answer("⛔ Нельзя удалить последнего супер-админа.")
        return

    await _save_access(access)
    who = f"@{uname}" if uname else uid
    await m.answer(f"✅ Отозван доступ СУПЕР-АДМИНА: {who}")

@_require_super
async def cmd_list_roles(m: Message, access: Dict[str, Any]):
    s = access["super"]
    a = access["admins"]
    txt = ["📜 Роли доступа:"]
    txt.append("\n🔶 Супер-админы:")
    lines = []
    if s["usernames"]:
        lines += [f"  • @{u}" for u in s["usernames"]]
    if s["ids"]:
        lines += [f"  • {i}" for i in s["ids"]]
    txt += lines or ["  —"]

    txt.append("\n🔹 Админы:")
    lines = []
    if a["usernames"]:
        lines += [f"  • @{u}" for u in a["usernames"]]
    if a["ids"]:
        lines += [f"  • {i}" for i in a["ids"]]
    txt += lines or ["  —"]

    await m.answer("\n".join(txt))

async def cmd_whoami(m: Message):
    acc = _load_access()
    role = "супер-админ" if is_super(m, acc) else ("админ" if is_admin(m, acc) else "нет доступа")
    uname = f"@{m.from_user.username}" if m.from_user.username else "(нет username)"
    await m.answer(f"Вы: {uname}\nID: {m.from_user.id}\nРоль: {role}")

# ---------- MAIN ----------
async def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN (переменная окружения).")
    bot = Bot(token)
    dp = Dispatcher()

    # базовые
    @dp.message(Command("start", "help"))
    async def start(m: Message):
        await m.answer(
            "Отправь видео — сделаю кружок Telegram.\n\n"
            "Роли:\n"
            "• Супер-админ — команды и конвертация\n"
            "• Админ — только конвертация\n"
            "• Остальным — доступ закрыт",
            reply_markup=MAIN_KB
        )

    @dp.message(Command("whoami"))
    async def who(m: Message):
        await cmd_whoami(m)

    # команды СУПЕР-АДМИНА
    @dp.message(Command("grant_admin"))
    async def _ga(m: Message): await cmd_grant_admin(m)
    @dp.message(Command("revoke_admin"))
    async def _ra(m: Message): await cmd_revoke_admin(m)
    @dp.message(Command("grant_super"))
    async def _gs(m: Message): await cmd_grant_super(m)
    @dp.message(Command("revoke_super"))
    async def _rs(m: Message): await cmd_revoke_super(m)
    @dp.message(Command("list_roles"))
    async def _lr(m: Message): await cmd_list_roles(m)

    # кнопки
    @dp.message(F.text == "🎥 Конвертировать видео"))
    async def ask(m: Message):
        ok, _ = await _ensure_admin_access_or_explain(m)
        if ok:
            await m.answer("Жду видео или пересланное видео.", reply_markup=MAIN_KB)

    @dp.message(F.text == "ℹ️ Помощь")
    async def help_(m: Message):
        await m.answer("Установи ffmpeg. Доступ только для ролей (админ/супер-админ).", reply_markup=MAIN_KB)

    # медиа
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
