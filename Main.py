import asyncio
import logging
import os
import shlex
import subprocess
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ChatActions
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"),
    ],
    force=True,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN must be set with your Telegram bot token.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


MAIN_KEYBOARD = (
    ReplyKeyboardMarkup(resize_keyboard=True)
    .add(KeyboardButton("🎥 Конвертировать видео"))
    .add(KeyboardButton("ℹ️ Помощь"))
)


async def convert_to_video_note(source_path: Path, destination_path: Path) -> None:
    """Convert a video file into a Telegram-compatible video note."""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        "crop='min(iw,ih)':'min(iw,ih)',scale=480:480,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "main",
        "-level",
        "3.1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(destination_path),
    ]

    logger.info("Running conversion command: %s", shlex.join(command))

    loop = asyncio.get_running_loop()
    process = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        ),
    )

    if process.returncode != 0:
        logger.error("ffmpeg error: %s", process.stderr)
        raise RuntimeError("Не удалось сконвертировать видео. Убедитесь, что установлен ffmpeg.")

    logger.info("Conversion finished successfully: %s", destination_path)


async def download_media(file_id: str, destination: Path) -> None:
    logger.info("Downloading file %s to %s", file_id, destination)
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination)
    logger.info("Download completed: %s", destination)


async def handle_video(message: types.Message, file_id: str, original_file_name: Optional[str]) -> None:
    await bot.send_chat_action(message.chat.id, ChatActions.UPLOAD_VIDEO_NOTE)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        source_suffix = Path(original_file_name).suffix if original_file_name else ".mp4"
        source_path = tmp_dir_path / f"source{source_suffix}"
        converted_path = tmp_dir_path / "converted.mp4"

        logger.info("Processing video for chat %s", message.chat.id)
        await download_media(file_id, source_path)

        try:
            await convert_to_video_note(source_path, converted_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to convert video note")
            await message.answer(str(exc))
            return

        await message.answer_video_note(types.InputFile(converted_path))
        logger.info("Video note sent to chat %s", message.chat.id)


@dp.message_handler(commands=["start", "help"])
async def send_welcome(message: types.Message) -> None:
    await message.answer(
        (
            "Привет! Отправь мне видео, и я превращу его в телеграмм-кружок.\n\n"
            "Можно загрузить новое видео или переслать уже существующее."
        ),
        reply_markup=MAIN_KEYBOARD,
    )


@dp.message_handler(lambda msg: msg.text == "🎥 Конвертировать видео")
async def prompt_for_video(message: types.Message) -> None:
    await message.answer(
        "Отправь или перешли видео, и я пришлю его в виде кружка.",
        reply_markup=MAIN_KEYBOARD,
    )


@dp.message_handler(lambda msg: msg.text == "ℹ️ Помощь")
async def send_help(message: types.Message) -> None:
    await message.answer(
        (
            "Этот бот принимает видео (в том числе пересланные) и возвращает их"
            " в формате кружка Telegram.\n\n"
            "Убедись, что установлен ffmpeg, если ты запускаешь бота локально."
        ),
        reply_markup=MAIN_KEYBOARD,
    )


@dp.message_handler(content_types=types.ContentType.VIDEO)
async def handle_uploaded_video(message: types.Message) -> None:
    video = message.video
    if not video:
        await message.answer("Не удалось получить видео из сообщения.")
        return

    await handle_video(message, video.file_id, video.file_name)


@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_document(message: types.Message) -> None:
    document = message.document
    if not document or not document.mime_type or not document.mime_type.startswith("video/"):
        await message.answer("Пожалуйста, отправь видео или пересланное видео.")
        return

    await handle_video(message, document.file_id, document.file_name)


@dp.message_handler(content_types=types.ContentType.ANIMATION)
async def handle_animation(message: types.Message) -> None:
    animation = message.animation
    if not animation:
        await message.answer("Не удалось получить анимацию.")
        return

    await handle_video(message, animation.file_id, animation.file_name)


if __name__ == "__main__":
    logger.info("Bot is starting...")
    executor.start_polling(dp, skip_updates=True)
