import os
import re
import io
import uuid
import shutil
import asyncio
import logging
import requests
from PIL import Image
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

TARGET_CHANNEL = os.environ.get("TARGET_CHANNEL", "")
TARGET_CHANNEL_USERNAME = os.environ.get("TARGET_CHANNEL_USERNAME", "")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

AUTO_SELECT_TIMEOUT = 15
AUTO_SELECT_PRIORITY = [480, 360, 240, 144, 720]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

STANDARD_LADDER = [2160, 1440, 1080, 720, 480, 360, 240, 144]

telethon_client = None


def _validate_config():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not SESSION_STRING:
        missing.append("SESSION_STRING")
    if missing:
        raise RuntimeError(
            "متغیرهای محیطی زیر ست نشدن: " + ", ".join(missing) +
            "\nقبل از اجرا این‌ها رو export کن یا توی فایل .env بذار."
        )


def _base_ydl_opts():
    opts = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "no_check_certificate": True,
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,
        "retries": 10,
        "fragment_retries": 10,
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    else:
        logger.warning(f"فایل کوکی پیدا نشد: {COOKIES_FILE} - دانلود بدون کوکی انجام می‌شه.")
    return opts


def extract_video_id(url):
    match = re.search(r"v=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    return None


def fetch_available_qualities(url):
    opts = _base_ydl_opts()
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return None, None, str(e)

    heights = set()
    for f in info.get("formats", []):
        height = f.get("height")
        ext = f.get("ext", "")
        if height and ext == "mp4":
            heights.add(height)

    if not heights:
        return None, None, "هیچ فرمت mp4ی برای این ویدیو پیدا نشد."

    return sorted(heights, reverse=True), info.get("title", "video"), None


def _prepare_thumbnail(info, video_id, unique_id):
    """
    تامبنیل ویدیو رو دانلود و برای تلگرام آماده می‌کنه (jpeg، حداکثر ۳۲۰ پیکسل،
    حجم کم). اگه هر مرحله fail بشه، None برمی‌گردونه و ویدیو بدون تامبنیل
    ارسال می‌شه — این باعث fail شدن کل دانلود نمی‌شه.
    """
    thumb_url = info.get("thumbnail")
    if not thumb_url:
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            thumb_url = thumbnails[-1].get("url")
    if not thumb_url:
        return None

    try:
        resp = requests.get(thumb_url, timeout=10)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.thumbnail((320, 320))
        thumb_path = f"{DOWNLOAD_DIR}/{video_id}_{unique_id}_thumb.jpg"
        img.save(thumb_path, "JPEG", quality=85)
        return thumb_path
    except Exception as e:
        logger.warning(f"دریافت/آماده‌سازی تامبنیل شکست خورد: {e}")
        return None


def download_video(url, quality, progress_hook=None):
    video_id = extract_video_id(url) or "video"
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{video_id}_{quality}_{unique_id}.%(ext)s"

    # به‌جای یک extract_info جدا برای انتخاب فرمت + یک دانلود جدا (که یعنی دو
    # درخواست شبکه‌ی اضافه)، از یک format selector استفاده می‌کنیم تا yt-dlp
    # خودش در همون یک مرحله‌ی دانلود، بهترین ویدیوی <= کیفیت انتخابی رو با
    # بهترین صدا merge کنه. این یک round-trip شبکه‌ی کامل رو حذف می‌کنه.
    fmt_string = (
        f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]"
        f"/best[ext=mp4][height<={quality}]"
        f"/best[ext=mp4]"
    )

    ydl_opts = {
        "format": fmt_string,
        "outtmpl": output_template,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "no_check_certificate": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,
        "retries": 10,
        "fragment_retries": 10,
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    # اگه aria2c نصب باشه، ازش به‌عنوان دانلودر خارجی استفاده کن — چون با چند
    # کانکشن همزمان دانلود می‌کنه و برای فایل‌های بزرگ معمولا خیلی سریع‌تره.
    if shutil.which("aria2c"):
        ydl_opts["external_downloader"] = "aria2c"
        ydl_opts["external_downloader_args"] = {
            "aria2c": ["-x", "16", "-s", "16", "-k", "1M"]
        }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        err = str(e)
        hint = ""
        if "Sign in to confirm" in err or "not a bot" in err.lower():
            hint = "\nاحتمالا نیاز به کوکی معتبر داری. فایل /root/cookies.txt رو بررسی و آپدیت کن."
        elif "Requested format is not available" in err:
            hint = "\nاین کیفیت برای این ویدیو موجود نیست، کیفیت دیگه‌ای رو امتحان کن."
        return None, None, None, f"خطا در دانلود: {err}{hint}"

    title = info.get("title", "video")
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)

    final_path = f"{DOWNLOAD_DIR}/{video_id}_{quality}_{unique_id}.mp4"
    if not os.path.exists(final_path):
        for ext in ["mp4", "webm", "mkv"]:
            candidate = f"{DOWNLOAD_DIR}/{video_id}_{quality}_{unique_id}.{ext}"
            if os.path.exists(candidate):
                final_path = candidate
                break

    if not os.path.exists(final_path):
        return None, None, None, "فایل پیدا نشد."

    file_size = os.path.getsize(final_path)
    if file_size < 100 * 1024:
        return None, None, None, "فایل خیلی کوچیکه (احتمالا ناقص)."

    thumb_path = _prepare_thumbnail(info, video_id, unique_id)

    return final_path, safe_title, thumb_path, "موفق"


async def send_to_channel(file_path, title, thumb_path=None, status=None):
    file_size = os.path.getsize(file_path)
    size_mb = file_size / (1024 * 1024)

    logger.info(f"شروع ارسال به کانال: {title} ({size_mb:.1f} MB)")

    last_logged_percent = {"value": -10}

    def progress_callback(sent_bytes, total_bytes):
        percent = int(sent_bytes / total_bytes * 100) if total_bytes else 0
        if status is not None:
            status["percent"] = percent
        if percent >= last_logged_percent["value"] + 10:
            last_logged_percent["value"] = percent
            logger.info(f"پیشرفت آپلود {title}: {percent}%")

    try:
        message = await asyncio.wait_for(
            telethon_client.send_file(
                TARGET_CHANNEL,
                file_path,
                caption=f"{title}\n{size_mb:.1f} MB",
                progress_callback=progress_callback,
                part_size_kb=512,
                thumb=thumb_path,
                supports_streaming=True,
            ),
            timeout=900,
        )

        logger.info(f"ارسال شد. Message ID: {message.id}")
        return message.id

    except asyncio.TimeoutError:
        logger.error(
            f"ارسال به کانال بعد از ۱۵ دقیقه هنوز تموم نشد (آخرین پیشرفت: "
            f"{max(last_logged_percent['value'], 0)}%). اتصال شبکه رو بررسی کن."
        )
        return None

    except Exception as e:
        logger.error(f"خطا در ارسال به کانال: {e}")
        return None


def _progress_bar(percent, width=12):
    percent = max(0, min(100, percent))
    filled = int(width * percent / 100)
    return "#" * filled + "-" * (width - filled)


async def _report_progress(bot, chat_id, message_id, status, label, stop_event):
    last_sent = -100
    while not stop_event.is_set():
        await asyncio.sleep(1.2)
        percent = status.get("percent", 0)
        if percent - last_sent >= 4 or (percent >= 100 and last_sent < 100):
            last_sent = percent
            bar = _progress_bar(percent)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{label}\n{bar} {percent}%",
                )
            except Exception:
                pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! لینک یوتیوب رو بفرست تا برات دانلود کنم.\n\n"
        "مثال:\nhttps://youtu.be/abc123\n"
        "https://www.youtube.com/watch?v=abc123"
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not re.match(r"https?://(www\.)?(youtube\.com|youtu\.be)/", url):
        await update.message.reply_text("لینک معتبر نیست!")
        return

    status_msg = await update.message.reply_text("در حال بررسی کیفیت‌های موجود...")

    loop = asyncio.get_running_loop()
    heights, title, error = await loop.run_in_executor(
        None, lambda: fetch_available_qualities(url)
    )

    if error:
        await status_msg.edit_text(f"خطا در گرفتن اطلاعات ویدیو: {error}")
        return

    offered = [h for h in STANDARD_LADDER if h in heights]
    if not offered:
        offered = heights

    auto_quality = next((q for q in AUTO_SELECT_PRIORITY if q in offered), offered[-1])

    token = uuid.uuid4().hex[:10]
    pending = context.user_data.setdefault("pending_downloads", {})
    pending[token] = {
        "url": url,
        "title": title,
        "handled": False,
        "auto_quality": auto_quality,
    }
    if len(pending) > 50:
        oldest_key = next(iter(pending))
        pending.pop(oldest_key, None)

    keyboard = [
        [InlineKeyboardButton(f"{h}p", callback_data=f"dl|{h}|{token}")]
        for h in offered
    ]
    await status_msg.edit_text(
        f"«{title}»\n"
        f"کیفیت رو انتخاب کن (تا {AUTO_SELECT_TIMEOUT} ثانیه دیگه انتخاب نکنی، "
        f"خودکار {auto_quality}p دانلود می‌شه):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        auto_select_callback,
        when=AUTO_SELECT_TIMEOUT,
        data={
            "token": token,
            "chat_id": status_msg.chat_id,
            "message_id": status_msg.message_id,
            "user_id": update.effective_user.id,
        },
        name=f"auto_select_{token}",
    )


async def auto_select_callback(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    token = job_data["token"]
    chat_id = job_data["chat_id"]
    message_id = job_data["message_id"]
    user_id = job_data["user_id"]

    user_data = context.application.user_data.get(user_id, {})
    pending = user_data.get("pending_downloads", {})
    entry = pending.get(token)

    if not entry or entry.get("handled"):
        return

    entry["handled"] = True
    quality = entry["auto_quality"]
    url = entry["url"]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=f"کیفیتی انتخاب نشد، خودکار {quality}p شروع شد...",
    )

    await process_download(
        context=context,
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        quality=quality,
        url=url,
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|", 2)
    if len(parts) < 3:
        return

    quality = int(parts[1])
    token = parts[2]
    user_id = query.message.chat_id

    pending = context.user_data.get("pending_downloads", {})
    entry = pending.get(token)
    if not entry:
        await query.edit_message_text(
            "این درخواست منقضی شده. لطفا لینک رو دوباره بفرست."
        )
        return

    if entry.get("handled"):
        return

    entry["handled"] = True
    url = entry["url"]

    await process_download(
        context=context,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        user_id=user_id,
        quality=quality,
        url=url,
    )


async def process_download(context, chat_id, message_id, user_id, quality, url):
    bot = context.bot
    loop = asyncio.get_running_loop()

    dl_status = {"percent": 0}
    dl_stop = asyncio.Event()
    dl_reporter = asyncio.create_task(
        _report_progress(bot, chat_id, message_id, dl_status, f"در حال دانلود {quality}p...", dl_stop)
    )

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes")
            if total and downloaded:
                dl_status["percent"] = int(downloaded / total * 100)
        elif d.get("status") == "finished":
            dl_status["percent"] = 100

    try:
        file_path, title, thumb_path, msg = await loop.run_in_executor(
            None, lambda: download_video(url, quality, progress_hook=hook)
        )
    finally:
        dl_stop.set()
        dl_reporter.cancel()

    if not file_path:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=msg)
        return

    file_size = os.path.getsize(file_path)
    size_mb = file_size / (1024 * 1024)

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=(
            f"دانلود کامل شد!\n"
            f"{title}\n"
            f"{size_mb:.1f} MB\n"
            f"در حال ارسال به کانال..."
        ),
    )

    try:
        up_status = {"percent": 0}
        up_stop = asyncio.Event()
        up_reporter = asyncio.create_task(
            _report_progress(bot, chat_id, message_id, up_status, "در حال ارسال به کانال...", up_stop)
        )
        try:
            channel_msg_id = await send_to_channel(
                file_path, title, thumb_path=thumb_path, status=up_status
            )
        finally:
            up_stop.set()
            up_reporter.cancel()

        if not channel_msg_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text="خطا یا timeout در ارسال به کانال! لاگ سرور رو چک کن.",
            )
            return

        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text="در حال فوروارد به شما..."
        )

        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=TARGET_CHANNEL_USERNAME,
                message_id=channel_msg_id,
            )

            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"فایل با موفقیت ارسال شد!\n\n"
                    f"{title}\n"
                    f"{size_mb:.1f} MB\n"
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
            )

        except Exception as e:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"فایل در کانال آپلود شد ولی فوروارد نشد.\n"
                    f"لطفا از کانال دانلود کنید:\n"
                    f"{TARGET_CHANNEL}/{channel_msg_id}\n\n"
                    f"خطا: {str(e)}"
                ),
            )
    finally:
        async def delete_later(path, thumb):
            await asyncio.sleep(30)
            for p in (path, thumb):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                        logger.info(f"فایل حذف شد: {p}")
                except Exception as e:
                    logger.error(f"خطا در حذف فایل {p}: {e}")

        task = asyncio.create_task(delete_later(file_path, thumb_path))
        context.application.bot_data.setdefault("cleanup_tasks", set()).add(task)
        task.add_done_callback(
            lambda t: context.application.bot_data.get("cleanup_tasks", set()).discard(t)
        )


async def main():
    global telethon_client

    _validate_config()

    telethon_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await telethon_client.start()

    me = await telethon_client.get_me()
    logger.info(f"Telethon لاگین شد به عنوان: {me.first_name}")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_click, pattern="^dl\\|"))

    logger.info("ربات شروع به کار کرد!")

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("در حال خاموش شدن...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await telethon_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())