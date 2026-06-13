import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# =====================
# CONFIG
# =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
REQUIRED_CHAT_ID_RAW = os.getenv("REQUIRED_CHAT_ID", "").strip()
REQUIRED_CHAT_LINK = os.getenv("REQUIRED_CHAT_LINK", "").strip()
TIMEZONE_NAME = os.getenv("TIMEZONE_NAME", "Asia/Kolkata").strip()

INITIAL_POINTS = 5
REFERRER_BONUS = 2
VIDEO_COST = 1
DELETE_AFTER_SECONDS = 300

DATA_FILE = Path("/data/data.json")

DATA_FILE.parent.mkdir(
    parents=True,
    exist_ok=True
)
if not DATA_FILE.exists():
    DATA_FILE.write_text(
        '{"users":{},"videos":[],"welcome_image_file_id":null,"welcome_messages":[],"buy_points_link":null,"forced_channels":[]}',
        encoding="utf-8"
    )
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Skipping invalid admin id: %s", part)
    return ids


ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)


def parse_chat_id(raw: str) -> int | str:
    raw = raw.strip()
    if raw.startswith("-") and raw[1:].isdigit():
        return int(raw)
    if raw.isdigit():
        return int(raw)
    return raw


REQUIRED_CHAT_ID: int | str = parse_chat_id(REQUIRED_CHAT_ID_RAW) if REQUIRED_CHAT_ID_RAW else ""


# =====================
# TIME HELPERS
# =====================

def now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(TIMEZONE_NAME))
    except Exception:
        return datetime.now(timezone.utc)


def today_key() -> str:
    return now_local().date().isoformat()


def timestamp() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


# =====================
# STORAGE
# =====================



def load_data() -> Dict[str, Any]:
    default = {
        "users": {},
        "videos": [],
        "welcome_image_file_id": None,
        "welcome_messages": [],
        "buy_points_link": None,
        "forced_channels": [],
    }
    if not DATA_FILE.exists():
        return default.copy()
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default.copy()
        if "users" not in data or not isinstance(data["users"], dict):
            data["users"] = {}
        if "videos" not in data or not isinstance(data["videos"], list):
            data["videos"] = []
        if "welcome_image_file_id" not in data:
            data["welcome_image_file_id"] = None
        if "welcome_messages" not in data or not isinstance(data["welcome_messages"], list):
            data["welcome_messages"] = []
        if "buy_points_link" not in data:
            data["buy_points_link"] = None
        if "forced_channels" not in data or not isinstance(data["forced_channels"], list):
            data["forced_channels"] = []
        return data
    except Exception as exc:
        logger.warning("Failed to read data.json: %s", exc)
        return default.copy()


def save_data(data: Dict[str, Any]) -> None:
    if "welcome_image_file_id" not in data:
        data["welcome_image_file_id"] = None
    if "welcome_messages" not in data or not isinstance(data["welcome_messages"], list):
        data["welcome_messages"] = []
    if "buy_points_link" not in data:
        data["buy_points_link"] = None
    if "forced_channels" not in data or not isinstance(data["forced_channels"], list):
        data["forced_channels"] = []
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)

def get_users(data: Dict[str, Any]) -> Dict[str, Any]:
    data.setdefault("users", {})
    return data["users"]


def get_videos(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    data.setdefault("videos", [])
    return data["videos"]


def get_welcome_image_file_id(data: Dict[str, Any]) -> str | None:
    return data.get("welcome_image_file_id")








def normalize_buy_points_link(value: str) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("@"):
        return f"https://t.me/{value.lstrip('@')}"
    return None


def get_buy_points_link(data: Dict[str, Any]) -> str | None:
    return normalize_buy_points_link(str(data.get("buy_points_link") or "").strip())


def set_buy_points_link(data: Dict[str, Any], value: str | None) -> None:
    data["buy_points_link"] = normalize_buy_points_link(value or "")


def get_welcome_messages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = data.setdefault("welcome_messages", [])
    if not isinstance(messages, list):
        messages = []
        data["welcome_messages"] = messages

    normalized: List[Dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "text").strip().lower()
        if kind in {"gif", "animation"}:
            kind = "animation"
        elif kind not in {"text", "photo", "video", "animation"}:
            kind = "text"

        entry: Dict[str, Any] = {
            "id": str(item.get("id") or f"welcome_{len(normalized) + 1}"),
            "kind": kind,
            "added_at": str(item.get("added_at") or timestamp()),
        }

        if kind == "text":
            text = str(item.get("text") or item.get("caption") or "").strip()
            if not text:
                continue
            entry["text"] = text
        else:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue
            entry["file_id"] = file_id
            caption = str(item.get("caption") or "").strip()
            if caption:
                entry["caption"] = caption

        normalized.append(entry)

    data["welcome_messages"] = normalized
    return normalized


def save_welcome_message(data: Dict[str, Any], kind: str, content: str, caption: str | None = None) -> None:
    messages = get_welcome_messages(data)
    entry: Dict[str, Any] = {
        "id": f"welcome_{len(messages) + 1}_{int(datetime.now().timestamp())}",
        "kind": kind,
        "added_at": timestamp(),
    }
    if kind == "text":
        entry["text"] = content.strip()
    else:
        entry["file_id"] = content.strip()
        if caption and caption.strip():
            entry["caption"] = caption.strip()
    messages.append(entry)
    data["welcome_messages"] = messages


def format_welcome_message_preview(entry: Dict[str, Any], index: int) -> str:
    kind = str(entry.get("kind") or "text")
    label = {
        "text": "📝 Text",
        "photo": "🖼 Photo",
        "video": "🎞 Video",
        "animation": "🎬 GIF",
    }.get(kind, "📝 Text")
    text = entry.get("text") or entry.get("caption") or entry.get("file_id") or ""
    text = str(text).strip()
    if len(text) > 55:
        text = text[:52] + "..."
    return f"{index}. {label} — {text}" if text else f"{index}. {label}"


def build_welcome_messages_text(data: Dict[str, Any]) -> str:
    messages = get_welcome_messages(data)
    if not messages:
        return (
            "📋 Welcome Messages\n\n"
            "No welcome messages saved yet.\n"
            "Use ➕ Add Welcome Message to create one."
        )

    lines = ["📋 Welcome Messages", "", f"Total: {len(messages)}", ""]
    for idx, entry in enumerate(messages, start=1):
        lines.append(format_welcome_message_preview(entry, idx))
    lines.append("")
    lines.append("Send the number after choosing 🗑 Remove Welcome Message.")
    return "\n".join(lines)


def build_buy_points_text(data: Dict[str, Any]) -> str:
    link = get_buy_points_link(data)
    if link:
        return f"💎 Buy Points Link\n\n{link}"
    return "💎 Buy Points Link\n\nNo link is set yet."


def build_buy_points_markup(data: Dict[str, Any]) -> InlineKeyboardMarkup | None:
    link = get_buy_points_link(data)
    if link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("💎 Buy Points", url=link)]])
    return None


def build_zero_points_markup(data: Dict[str, Any]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton("👥 Refer Friends", callback_data="refer")]]
    link = get_buy_points_link(data)
    if link:
        rows.append([InlineKeyboardButton("💎 Buy Points", url=link)])
    else:
        rows.append([InlineKeyboardButton("💎 Buy Points", callback_data="buy_points_info")])
    return InlineKeyboardMarkup(rows)


def build_how_to_use_text() -> str:
    return (
        "✨ How To Use\n\n"
        "1️⃣ Join channels\n"
        "2️⃣ Get free points\n"
        "3️⃣ Use 🎬 GET VIDEO 🎬\n"
        "4️⃣ Refer friends\n"
        "5️⃣ Earn more points"
    )


def build_start_intro_text(username: str, points: int) -> str:
    return (
        f"👋 Welcome, {username}!\n\n"
        f"⭐ Your points: {points}\n"
        "Use the buttons below to continue."
    )
def get_forced_channels(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    channels = data.setdefault("forced_channels", [])
    if not isinstance(channels, list):
        channels = []
        data["forced_channels"] = channels

    normalized: List[Dict[str, Any]] = []
    for item in channels:
        if isinstance(item, dict):
            chat_id = item.get("chat_id")
            if chat_id is None:
                continue
            normalized.append(
                {
                    "chat_id": chat_id,
                    "title": str(item.get("title") or str(chat_id)).strip(),
                    "link": str(item.get("link") or "").strip() or None,
                }
            )

    data["forced_channels"] = normalized
    return normalized




def legacy_required_channel_entry() -> Dict[str, Any] | None:
    if not REQUIRED_CHAT_ID:
        return None

    link = REQUIRED_CHAT_LINK.strip() if REQUIRED_CHAT_LINK else ""
    if not link and isinstance(REQUIRED_CHAT_ID, str) and REQUIRED_CHAT_ID.startswith("@"):
        link = f"https://t.me/{REQUIRED_CHAT_ID.lstrip('@')}"
    return {
        "chat_id": REQUIRED_CHAT_ID,
        "title": "Main Join Channel",
        "link": link or None,
    }

def normalize_channel_chat_id(value: str) -> int | str:
    value = value.strip()
    if value.startswith("-") and value[1:].isdigit():
        return int(value)
    if value.startswith("@"):
        return value
    if value.isdigit():
        return int(value)
    return value


def normalize_channel_link(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("@"):
        return f"https://t.me/{value.lstrip('@')}"
    return None


def channel_join_link(channel: Dict[str, Any]) -> str | None:
    link = channel.get("link")
    if link:
        return str(link).strip() or None

    chat_id = channel.get("chat_id")
    if isinstance(chat_id, str) and chat_id.startswith("@"):
        return f"https://t.me/{chat_id.lstrip('@')}"
    return None


def channel_label(channel: Dict[str, Any], index: int) -> str:
    title = str(channel.get("title") or "").strip()
    if title:
        return title
    chat_id = channel.get("chat_id")
    if isinstance(chat_id, str) and chat_id.startswith("@"):
        return chat_id.lstrip("@")
    return f"Channel {index}"


def get_all_required_channels(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    legacy = legacy_required_channel_entry()
    if legacy:
        channels.append(legacy)
    channels.extend(get_forced_channels(data))

    unique: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for channel in channels:
        chat_id = channel.get("chat_id")
        if chat_id is None:
            continue
        key = str(chat_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(channel)
    return unique[:5]


def parse_forced_channel_input(raw: str) -> tuple[Dict[str, Any] | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, "Send a channel username like @channelname, or use -1001234567890|https://t.me/+invite for private channels."

    chat_part = raw
    link_part = ""
    if "|" in raw:
        chat_part, link_part = [part.strip() for part in raw.split("|", 1)]

    chat_id = normalize_channel_chat_id(chat_part)
    link = normalize_channel_link(link_part) if link_part else None

    if isinstance(chat_id, str) and chat_id.startswith("@"):
        title = chat_id.lstrip("@")
        if not link:
            link = f"https://t.me/{title}"
        return {"chat_id": chat_id, "title": title, "link": link}, None

    if isinstance(chat_id, int):
        if not link:
            return None, (
                "For private channels, send the channel id and invite link like:\n"
                "-1001234567890|https://t.me/+invite-link"
            )
        return {"chat_id": chat_id, "title": f"Channel {chat_id}", "link": link}, None

    if isinstance(chat_id, str) and chat_id.startswith("https://t.me/"):
        username = chat_id.rstrip("/").split("/")[-1]
        if username.startswith("+"):
            return None, (
                "Invite links alone cannot be used for membership checks. Send a public channel username or a chat id with invite link."
            )
        chat_id = f"@{username.lstrip('@')}"
        title = username.lstrip("@")
        if not link:
            link = f"https://t.me/{title}"
        return {"chat_id": chat_id, "title": title, "link": link}, None

    return None, (
        "Invalid channel format. Use @channelname for public channels or -1001234567890|https://t.me/+invite for private channels."
    )




def build_forced_channels_keyboard(data: Dict[str, Any]) -> InlineKeyboardMarkup:
    channels = get_all_required_channels(data)
    buttons: List[List[InlineKeyboardButton]] = []

    for idx, channel in enumerate(channels, start=1):
        link = channel_join_link(channel)
        label = f"{idx}. {channel_label(channel, idx)}"
        if link:
            buttons.append([InlineKeyboardButton(label, url=link)])
        else:
            buttons.append([InlineKeyboardButton(label, callback_data="no_join_link")])

    buttons.append([InlineKeyboardButton("➕ Add Channel", callback_data="forced_channels_add")])
    buttons.append([InlineKeyboardButton("➖ Remove Channel", callback_data="forced_channels_remove")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="forced_channels_back")])
    return InlineKeyboardMarkup(buttons)


def build_forced_channels_text(data: Dict[str, Any]) -> str:
    channels = get_all_required_channels(data)
    if not channels:
        return "🔗 Join Channels\n\nNo join channels are configured yet.\nUse ➕ Add Channel to add up to 5 channels."

    lines = ["🔗 Join Channels", "", f"Total required channels: {len(channels)}/5", ""]
    for idx, channel in enumerate(channels, start=1):
        lines.append(f"{idx}. {channel_label(channel, idx)}")
    lines.append("")
    lines.append("Users must join all of them before using the bot.")
    return "\n".join(lines)

def get_user(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    users = get_users(data)
    key = str(user_id)
    if key not in users:
        users[key] = {
            "points": 0,
            "joined": False,
            "activated": False,
            "referrer": None,
            "pending_referrer": None,
            "referred_count": 0,
            "milestone_rewards_claimed": 0,
            "daily_bonus_date": None,
            "last_bonus_reminder": None,
            "video_index": 0,
            "videos_sent": 0,
            "first_seen": None,
            "joined_at": None,
            "last_seen": None,
            "last_active_date": None,
            "total_uses": 0,
            "inactive_reminder_sent": False,
        }
    user = users[key]
    user.setdefault("points", 0)
    user.setdefault("joined", False)
    user.setdefault("activated", False)
    user.setdefault("referrer", None)
    user.setdefault("pending_referrer", None)
    user.setdefault("referred_count", 0)
    user.setdefault("milestone_rewards_claimed", 0)
    user.setdefault("daily_bonus_date", None)
    user.setdefault("last_bonus_reminder", None)
    user.setdefault("video_index", 0)
    user.setdefault("videos_sent", 0)
    user.setdefault("first_seen", None)
    user.setdefault("joined_at", None)
    user.setdefault("last_seen", None)
    user.setdefault("last_active_date", None)
    user.setdefault("total_uses", 0)
    user.setdefault("inactive_reminder_sent", False)
    return user


def touch_user(data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    user = get_user(data, user_id)
    day = today_key()
    if not user.get("first_seen"):
        user["first_seen"] = timestamp()
    user["last_seen"] = timestamp()
    user["last_active_date"] = day
    user["total_uses"] = int(user.get("total_uses", 0)) + 1
    user["inactive_reminder_sent"] = False
    return user


# =====================
# HELPERS
# =====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def menu_for_user(user_id: int):
    return admin_menu() if is_admin(user_id) else user_menu()


async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int | str | None = None) -> bool:
    if chat_id is not None:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.status in ("member", "administrator", "creator")
        except Exception as exc:
            logger.warning("Join check failed for %s: %s", chat_id, exc)
            return False

    data = load_data()
    channels = get_all_required_channels(data)
    if not channels:
        return True

    for channel in channels:
        if not await is_member(context, user_id, channel.get("chat_id")):
            return False
    return True




def user_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🎬 GET VIDEO 🎬")],
            [KeyboardButton("👤 Dashboard"), KeyboardButton("⭐ My Points")],
            [KeyboardButton("👥 Refer & Earn"), KeyboardButton("🎁 Daily Bonus")],
            [KeyboardButton("❓ Help"), KeyboardButton("💎 Buy Points")],
        ],
        resize_keyboard=True,
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🛠 Admin Dashboard"), KeyboardButton("📢 Broadcast")],
            [KeyboardButton("🖼 Set Welcome Image"), KeyboardButton("➕ Add Video")],
            [KeyboardButton("📋 List Videos"), KeyboardButton("🔗 Join Channels")],
            [KeyboardButton("➕ Add Welcome Message"), KeyboardButton("🗑 Remove Welcome Message")],
            [KeyboardButton("📋 List Welcome Messages"), KeyboardButton("💎 Set Buy Points Link")],
            [KeyboardButton("👀 View Buy Points Link"), KeyboardButton("❌ Remove Buy Points Link")],
        ],
        resize_keyboard=True,
    )


def join_keyboard() -> InlineKeyboardMarkup:
    data = load_data()
    channels = get_all_required_channels(data)
    buttons: List[List[InlineKeyboardButton]] = []

    if channels:
        for idx, channel in enumerate(channels, start=1):
            link = channel_join_link(channel)
            label = f"🔗 Join {channel_label(channel, idx)}"
            if link:
                buttons.append([InlineKeyboardButton(label, url=link)])
            else:
                buttons.append([InlineKeyboardButton(label, callback_data="no_join_link")])
    else:
        if REQUIRED_CHAT_LINK:
            buttons.append([InlineKeyboardButton("🔗 Join Channel / Group", url=REQUIRED_CHAT_LINK)])
        elif isinstance(REQUIRED_CHAT_ID, str) and REQUIRED_CHAT_ID.startswith("@"):
            buttons.append(
                [InlineKeyboardButton("🔗 Join Channel / Group", url=f"https://t.me/{REQUIRED_CHAT_ID.lstrip('@')}")]
            )
        else:
            buttons.append([InlineKeyboardButton("🔗 Join Channel / Group", callback_data="no_join_link")])

    buttons.append([InlineKeyboardButton("✅ I Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)


def referral_share_markup(referral_link: str) -> InlineKeyboardMarkup:
    share_url = f"https://t.me/share/url?{urlencode({'url': referral_link, 'text': 'Join and earn free points 💎'})}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 Share Referral", url=share_url)]
        ]
    )


def get_refer_button_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👥 Refer & Earn", callback_data="refer")]]
    )


async def send_join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    channels = get_all_required_channels(data)
    if channels:
        lines = [
            "🔐 Access Required",
            "",
            "Please join all required channels/groups first.",
            "",
            "After joining, press ✅ I Joined:",
            "",
        ]
        for idx, channel in enumerate(channels, start=1):
            lines.append(f"{idx}. {channel_label(channel, idx)}")
        text = "\n".join(lines)
    else:
        text = (
            "🔐 Access Required\n\n"
            "Please join our channel/group first to use this bot.\n\n"
            "After joining, press ✅ I Joined."
        )

    if update.message:
        await update.message.reply_text(text, reply_markup=join_keyboard())
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=join_keyboard())


async def send_welcome_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str) -> None:
    data = load_data()
    points = get_user(data, update.effective_user.id).get("points", 0)
    welcome_messages = get_welcome_messages(data)
    legacy_welcome_image_file_id = get_welcome_image_file_id(data)

    if update.message is None:
        return

    sent_custom_content = False

    if welcome_messages:
        for entry in welcome_messages:
            kind = entry.get("kind", "text")
            try:
                if kind == "text":
                    await update.message.reply_text(str(entry.get("text") or ""))
                elif kind == "photo":
                    await update.message.reply_photo(
                        photo=entry["file_id"],
                        caption=entry.get("caption") or None,
                    )
                elif kind == "video":
                    await update.message.reply_video(
                        video=entry["file_id"],
                        caption=entry.get("caption") or None,
                    )
                elif kind == "animation":
                    await update.message.reply_animation(
                        animation=entry["file_id"],
                        caption=entry.get("caption") or None,
                    )
                sent_custom_content = True
            except Exception as exc:
                logger.warning("Failed to send welcome message item: %s", exc)
    elif legacy_welcome_image_file_id:
        try:
            await update.message.reply_photo(
                photo=legacy_welcome_image_file_id,
                caption=build_start_intro_text(username, points),
            )
            sent_custom_content = True
        except Exception as exc:
            logger.warning("Failed to send welcome image: %s", exc)

    if not sent_custom_content and not welcome_messages:
        await update.message.reply_text(build_start_intro_text(username, points))

    await update.message.reply_text(build_how_to_use_text(), reply_markup=menu_for_user(update.effective_user.id))

async def delete_sent_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    data = job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if not chat_id or not message_id:
        return

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        logger.warning(
            "Failed to delete message %s in chat %s: %s",
            message_id,
            chat_id,
            exc,
        )




async def activate_user_if_needed(
    data: Dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> None:
    user = get_user(data, user_id)
    if user["activated"]:
        return

    user["activated"] = True
    user["joined"] = True
    user["joined_at"] = today_key()
    user["points"] += INITIAL_POINTS

    pending_referrer = user.get("pending_referrer")
    if pending_referrer:
        try:
            referrer_id = int(pending_referrer)
            if referrer_id != user_id:
                referrer = get_user(data, referrer_id)
                user["referrer"] = referrer_id
                referrer["points"] += REFERRER_BONUS
                referrer["referred_count"] = int(referrer.get("referred_count", 0)) + 1

                await apply_referral_milestone_reward(data, context, referrer_id)

                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            "🎉 Referral Success!\n\n"
                            "Your friend just joined through your link.\n"
                            f"💎 +{REFERRER_BONUS} points added.\n"
                            f"👥 Total referrals: {referrer['referred_count']}\n"
                            f"⭐ Current points: {referrer['points']}"
                        ),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to notify referrer %s: %s",
                        referrer_id,
                        e,
                    )
        except Exception:
            logger.warning("Invalid pending_referrer for user %s", user_id)

    user["pending_referrer"] = None
    save_data(data)


async def apply_referral_milestone_reward(data: Dict[str, Any], context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    user = get_user(data, user_id)
    referrals = int(user.get("referred_count", 0))
    claimed = int(user.get("milestone_rewards_claimed", 0))
    earned_levels = referrals // 5

    if earned_levels <= claimed:
        return

    new_levels = earned_levels - claimed
    bonus = new_levels * 3
    user["points"] += bonus
    user["milestone_rewards_claimed"] = earned_levels
    save_data(data)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"🏆 Referral Milestone Unlocked!\n\n"
                f"You reached {earned_levels * 5} referrals.\n"
                f"🎁 Bonus points added: +{bonus}\n"
                f"⭐ Total points: {user['points']}"
            ),
        )
    except Exception as exc:
        logger.warning("Failed to notify milestone reward for %s: %s", user_id, exc)

async def ensure_joined_or_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False

    user_id = update.effective_user.id
    data = load_data()
    touch_user(data, user_id)
    save_data(data)

    if await is_member(context, user_id):
        user = get_user(data, user_id)
        user["joined"] = True
        await activate_user_if_needed(data, context, user_id)
        return True

    await send_join_gate(update, context)
    return False


# =====================
# DASHBOARDS
# =====================



def build_user_dashboard(user: Dict[str, Any], total_videos: int) -> str:
    points = int(user.get("points", 0))
    referrals = int(user.get("referred_count", 0))
    videos_sent = int(user.get("videos_sent", 0))
    next_video = (int(user.get("video_index", 0)) % total_videos) + 1 if total_videos else 0

    lines = [
        "👤 Your Dashboard",
        "",
        f"⭐ Points: {points}",
        f"🎬 Videos received: {videos_sent}",
        f"👥 Successful referrals: {referrals}",
    ]

    if total_videos:
        lines.append(f"📺 Next video number: {next_video}")

    lines.append("")
    if points <= 0:
        lines.append("😢 Oops! You have no points left.")
        lines.append("👥 Refer friends to earn more points.")
        lines.append("💎 Buy points to unlock videos instantly.")
    elif points < VIDEO_COST:
        lines.append("😢 You do not have enough points right now.")
        lines.append("👥 Refer friends to earn more points.")
        lines.append("💎 Buy points to unlock videos instantly.")
    else:
        lines.append(f"✅ You can unlock {points // VIDEO_COST} more video request(s) right now.")
        lines.append("Keep your points moving by inviting more people.")

    lines.append("")
    lines.append("✨ Simple path: join → earn → watch → refer → repeat.")
    return "\n".join(lines)

def build_admin_dashboard(data: Dict[str, Any]) -> str:
    users = get_users(data)
    day = today_key()

    total_users = len(users)
    joined_today = sum(1 for u in users.values() if u.get("joined_at") == day)
    active_today = sum(1 for u in users.values() if u.get("last_active_date") == day)
    total_referrals = sum(int(u.get("referred_count", 0)) for u in users.values())
    total_points = sum(int(u.get("points", 0)) for u in users.values())
    total_videos_sent = sum(int(u.get("videos_sent", 0)) for u in users.values())
    total_uses = sum(int(u.get("total_uses", 0)) for u in users.values())
    total_videos = len(get_videos(data))

    return (
        "🛠 Admin Dashboard\n\n"
        f"👥 Total users: {total_users}\n"
        f"🆕 Joined today: {joined_today}\n"
        f"✅ Active today: {active_today}\n"
        f"🔁 Total referrals: {total_referrals}\n"
        f"🎬 Total videos stored: {total_videos}\n"
        f"📤 Total videos sent: {total_videos_sent}\n"
        f"⭐ Total points in system: {total_points}\n"
        f"📊 Total bot uses: {total_uses}\n\n"
        "This panel shows growth, activity, and delivery numbers."
    )


async def send_all_videos_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    videos = get_videos(data)
    message = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id

    if not videos:
        if message:
            await message.reply_text("🎞 🎞 Saved videos: 0")
        return

    if message:
        await message.reply_text(f"🎞 🎞 Saved videos: {len(videos)}")

    if len(videos) == 1:
        try:
            await context.bot.send_video(
                chat_id=chat_id,
                video=videos[0]["file_id"],
            )
        except Exception as exc:
            logger.warning("Failed to send single video: %s", exc)
        return

    chunk_size = 10
    for start in range(0, len(videos), chunk_size):
        chunk = videos[start:start + chunk_size]
        media = [InputMediaVideo(media=video["file_id"]) for video in chunk]
        try:
            await context.bot.send_media_group(
                chat_id=chat_id,
                media=media,
            )
        except Exception as exc:
            logger.warning("Failed to send media group: %s", exc)
            for video in chunk:
                try:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=video["file_id"],
                    )
                except Exception as exc2:
                    logger.warning("Failed to send video %s: %s", video.get("title"), exc2)




async def send_referral_message(message, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    data = load_data()
    user = get_user(data, user_id)
    save_data(data)

    text = (
        "😘 Hey there!\n\n"
        "Invite your friends and earn more points ❤️\n\n"
        f"🔗 🔗 Your referral link:\n{referral_link}\n\n"
        f"⭐ Current points: {user['points']}\n"
        f"🎁 You get {REFERRER_BONUS} points for every successful referral."
    )

    await message.reply_text(
        text,
        reply_markup=referral_share_markup(referral_link),
    )


async def send_next_video(message, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    data = load_data()
    user = get_user(data, user_id)
    videos = get_videos(data)

    if not videos:
        await message.reply_text(
            "🎬 No videos have been added yet. Admin must add videos first.",
            reply_markup=menu_for_user(user_id),
        )
        return

    if user["points"] < VIDEO_COST:
        await message.reply_text(
            "😢 Oops! You have no points left.",
            reply_markup=build_zero_points_markup(data),
        )
        return

    index = int(user.get("video_index", 0)) % len(videos)
    video = videos[index]
    user["video_index"] = (index + 1) % len(videos)
    user["points"] -= VIDEO_COST
    user["videos_sent"] = int(user.get("videos_sent", 0)) + 1
    save_data(data)

    try:
        sent_message = await message.reply_video(
            video=video["file_id"],
            caption=(
                f"🎬 Here is your video!\n\n"
                f"⭐ Points left: {user['points']}\n\n"
                "Forward or save this video; it will be deleted after 5 minutes."
            ),
            reply_markup=get_refer_button_markup(),
        )

        if context.job_queue is not None:
            context.job_queue.run_once(
                delete_sent_message,
                DELETE_AFTER_SECONDS,
                data={
                    "chat_id": sent_message.chat_id,
                    "message_id": sent_message.message_id,
                },
            )
    except Exception as exc:
        logger.warning("Failed to send saved video: %s", exc)
        await message.reply_text(
            "⚠️ I could not send that video file. Admin may need to re-upload it.",
            reply_markup=menu_for_user(user_id),
        )

# =====================
# USER ACTIONS
# =====================


async def do_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await ensure_joined_or_gate(update, context):
        return

    data = load_data()
    user = get_user(data, update.effective_user.id)
    text = build_user_dashboard(user, len(get_videos(data)))
    save_data(data)
    await update.message.reply_text(text, reply_markup=menu_for_user(update.effective_user.id))



async def do_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await ensure_joined_or_gate(update, context):
        return

    data = load_data()
    user = get_user(data, update.effective_user.id)
    points = int(user.get("points", 0))
    save_data(data)

    if points <= 0:
        await update.message.reply_text(
            "😢 Oops! You have no points left.",
            reply_markup=build_zero_points_markup(data),
        )
        return

    await update.message.reply_text(
        f"⭐ Your points: {points}",
        reply_markup=menu_for_user(update.effective_user.id),
    )

async def do_refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await ensure_joined_or_gate(update, context):
        return

    await send_referral_message(update.message, context, update.effective_user.id)


async def do_get_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await ensure_joined_or_gate(update, context):
        return

    await send_next_video(update.message, context, update.effective_user.id)




async def do_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await ensure_joined_or_gate(update, context):
        return

    data = load_data()
    user = get_user(data, update.effective_user.id)
    today = today_key()

    if user.get("daily_bonus_date") == today:
        await update.message.reply_text(
            "🎁 Daily bonus already claimed today.\n\nCome back tomorrow for another free point.",
            reply_markup=menu_for_user(update.effective_user.id),
        )
        return

    user["points"] += 1
    user["daily_bonus_date"] = today
    save_data(data)

    await update.message.reply_text(
        f"🎁 Daily bonus claimed!\n\nYou got +1 point.\n⭐ New balance: {user['points']}",
        reply_markup=menu_for_user(update.effective_user.id),
    )

async def show_forced_channels_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    data = load_data()
    await update.message.reply_text(
        build_forced_channels_text(data),
        reply_markup=build_forced_channels_keyboard(data),
    )


# =====================
# COMMAND HANDLERS
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.full_name
    data = load_data()
    touch_user(data, user_id)
    user = get_user(data, user_id)

    if not user["activated"] and context.args and not user.get("pending_referrer"):
        ref = context.args[0]
        try:
            referrer_id = int(ref)
            if referrer_id != user_id:
                user["pending_referrer"] = referrer_id
                save_data(data)
        except ValueError:
            pass

    if not await is_member(context, user_id):
        save_data(data)
        await send_join_gate(update, context)
        return

    await activate_user_if_needed(data, context, user_id)
    user = get_user(data, user_id)
    save_data(data)

    await send_welcome_to_user(update, context, username)


async def points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_points(update, context)


async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_refer(update, context)


async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_daily_bonus(update, context)


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await do_dashboard(update, context)




async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    help_text = (
        "❓ Help Center\n\n"
        "Need help using the bot?\n\n"
        "📩 Contact: @jainsahiba18\n\n"
        "✨ Use 🎬 GET VIDEO 🎬 to unlock videos.\n"
        "👥 Refer friends to earn more points.\n"
        "🎁 Claim Daily Bonus once every 24 hours.\n"
        "🔄 Stay joined in the required channels to keep using the bot.\n\n"
        "Thank you for using our bot ❤️"
    )

    if is_admin(update.effective_user.id):
        await update.message.reply_text(help_text, reply_markup=admin_menu())
    else:
        await update.message.reply_text(help_text, reply_markup=user_menu())


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    data = load_data()
    touch_user(data, update.effective_user.id)
    save_data(data)
    await update.message.reply_text(build_admin_dashboard(data), reply_markup=admin_menu())


async def add_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["add_video_mode"] = True
    await update.message.reply_text(
        "🎞 Send me a video now. I will save its Telegram file_id, so you do not need local storage.",
        reply_markup=admin_menu(),
    )


async def list_videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    await send_all_videos_to_admin(update, context)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["broadcast_mode"] = True
    await update.message.reply_text(
        "📢 Send text, photo, video, GIF, or document to broadcast.",
        reply_markup=admin_menu(),
    )


async def set_welcome_image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["set_welcome_image_mode"] = True
    await update.message.reply_text(
        "🖼 Send the welcome image now. I will save it and use it on /start.",
        reply_markup=admin_menu(),
    )




async def add_welcome_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["welcome_message_mode"] = "add"
    await update.message.reply_text(
        "➕ Send a welcome message now. You can send text, photo, video, or GIF.",
        reply_markup=admin_menu(),
    )


async def remove_welcome_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["welcome_message_mode"] = "remove"
    data = load_data()
    await update.message.reply_text(
        build_welcome_messages_text(data) + "\n\nSend the number to remove, or type ALL to clear everything.",
        reply_markup=admin_menu(),
    )


async def list_welcome_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    data = load_data()
    await update.message.reply_text(
        build_welcome_messages_text(data),
        reply_markup=admin_menu(),
    )


async def set_buy_points_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    context.user_data["buy_points_link_mode"] = "set"
    await update.message.reply_text(
        "💎 Send the buy points link now.",
        reply_markup=admin_menu(),
    )


async def view_buy_points_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    data = load_data()
    link = get_buy_points_link(data)
    if link:
        await update.message.reply_text(
            f"👀 Current Buy Points Link:\n{link}",
            reply_markup=admin_menu(),
        )
    else:
        await update.message.reply_text(
            "👀 No buy points link is set yet.",
            reply_markup=admin_menu(),
        )


async def remove_buy_points_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    data = load_data()
    data["buy_points_link"] = None
    save_data(data)
    await update.message.reply_text(
        "❌ Buy points link removed.",
        reply_markup=admin_menu(),
    )
# =====================
# CALLBACK HANDLER
# =====================



async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.effective_user or not update.callback_query.message:
        return

    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    if query.data == "no_join_link":
        await query.message.reply_text("⚠️ Set REQUIRED_CHAT_LINK for the join button if the group/channel is private.")
        return

    if query.data == "check_join":
        if await is_member(context, user_id):
            data = load_data()
            touch_user(data, user_id)
            await activate_user_if_needed(data, context, user_id)
            user = get_user(data, user_id)
            save_data(data)
            await query.message.reply_text(
                f"✅ ✅ Verified. You can now use the bot.\n\n⭐ Points: {user['points']}",
                reply_markup=menu_for_user(user_id),
            )
        else:
            await query.message.reply_text(
                "⚠️ You still have not joined. Join first, then press again.",
                reply_markup=join_keyboard(),
            )
        return

    if query.data == "admin_dashboard" and is_admin(user_id):
        data = load_data()
        touch_user(data, user_id)
        save_data(data)
        await query.message.reply_text(build_admin_dashboard(data), reply_markup=admin_menu())
        return

    if query.data == "set_welcome_image" and is_admin(user_id):
        context.user_data["set_welcome_image_mode"] = True
        await query.message.reply_text(
            "🖼 Send the welcome image now. I will save it and use it on /start.",
            reply_markup=admin_menu(),
        )
        return

    if query.data == "add_video" and is_admin(user_id):
        context.user_data["add_video_mode"] = True
        await query.message.reply_text(
            "🎞 Send me a video now. I will store its Telegram file_id and use it for future delivery.",
            reply_markup=admin_menu(),
        )
        return

    if query.data == "list_videos" and is_admin(user_id):
        await send_all_videos_to_admin(update, context)
        return

    if query.data == "admin_broadcast" and is_admin(user_id):
        context.user_data["broadcast_mode"] = True
        await query.message.reply_text(
            "📢 Send the message you want to broadcast to all users.",
            reply_markup=admin_menu(),
        )
        return

    if query.data == "forced_channels_back" and is_admin(user_id):
        context.user_data["forced_channels_add_mode"] = False
        context.user_data["forced_channels_remove_mode"] = False
        await query.message.reply_text("⬅️ Back to admin menu.", reply_markup=admin_menu())
        return

    if query.data == "forced_channels_add" and is_admin(user_id):
        context.user_data["forced_channels_add_mode"] = True
        await query.message.reply_text(
            "Send the channel in one of these formats:\n"
            "@publicchannel\n"
            "-1001234567890|https://t.me/+invite-link",
            reply_markup=admin_menu(),
        )
        return

    if query.data == "forced_channels_remove" and is_admin(user_id):
        data = load_data()
        channels = get_all_required_channels(data)
        if not channels:
            await query.message.reply_text(
                "No channels available to remove.",
                reply_markup=admin_menu(),
            )
            return

        context.user_data["forced_channels_remove_mode"] = True

        lines = ["Send channel number to remove:", ""]
        for idx, channel in enumerate(channels, start=1):
            lines.append(f"{idx}. {channel_label(channel, idx)}")

        await query.message.reply_text(
            "\n".join(lines),
            reply_markup=admin_menu(),
        )
        return

    if query.data == "refer":
        if not await ensure_joined_or_gate(update, context):
            return
        await send_referral_message(query.message, context, user_id)
        return

    if query.data == "get_video":
        if not await ensure_joined_or_gate(update, context):
            return
        await send_next_video(query.message, context, user_id)
        return

    if query.data == "buy_points_info":
        data = load_data()
        link = get_buy_points_link(data)
        if link:
            await query.message.reply_text(
                f"💎 Buy points here:\n{link}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Buy Points", url=link)]]),
            )
        else:
            await query.message.reply_text(
                "🚧 Coming Soon\nContact @jainsahiba18",
            )
        return

# =====================
# MESSAGE HANDLERS
# =====================



async def handle_admin_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("broadcast_mode"):
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            content_type = "photo"
        elif update.message.animation:
            file_id = update.message.animation.file_id
            content_type = "animation"
        else:
            return

        try:
            sent, failed = await execute_broadcast(context, content_type=content_type, file_id=file_id, caption=update.message.caption)
            await update.message.reply_text(
                f"✅ Broadcast done. Sent: {sent}, Failed: {failed}",
                reply_markup=admin_menu(),
            )
        finally:
            context.user_data.pop("broadcast_mode", None)
        return

    if context.user_data.get("welcome_message_mode") == "add":
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            data = load_data()
            save_welcome_message(data, "photo", file_id, update.message.caption)
            save_data(data)
            context.user_data["welcome_message_mode"] = None
            await update.message.reply_text("✅ Welcome photo saved.", reply_markup=admin_menu())
            return
        if update.message.animation:
            file_id = update.message.animation.file_id
            data = load_data()
            save_welcome_message(data, "animation", file_id, update.message.caption)
            save_data(data)
            context.user_data["welcome_message_mode"] = None
            await update.message.reply_text("✅ Welcome GIF saved.", reply_markup=admin_menu())
            return

    if not context.user_data.get("set_welcome_image_mode"):
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Please send a valid image.")
        return

    data = load_data()
    data["welcome_image_file_id"] = file_id
    save_data(data)
    context.user_data["set_welcome_image_mode"] = False

    await update.message.reply_text(
        "✅ Welcome image saved successfully.",
        reply_markup=admin_menu(),
    )


async def handle_admin_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("broadcast_mode"):
        file_id = None
        if update.message.video:
            file_id = update.message.video.file_id
        elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/"):
            file_id = update.message.document.file_id
        
        if file_id:
            try:
                sent, failed = await execute_broadcast(context, content_type="video", file_id=file_id, caption=update.message.caption)
                await update.message.reply_text(
                    f"✅ Broadcast done. Sent: {sent}, Failed: {failed}",
                    reply_markup=admin_menu(),
                )
            finally:
                context.user_data.pop("broadcast_mode", None)
        return

    if context.user_data.get("welcome_message_mode") == "add":
        if update.message.video:
            file_id = update.message.video.file_id
            data = load_data()
            save_welcome_message(data, "video", file_id, update.message.caption)
            save_data(data)
            context.user_data["welcome_message_mode"] = None
            await update.message.reply_text("✅ Welcome video saved.", reply_markup=admin_menu())
            return
        if update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/"):
            file_id = update.message.document.file_id
            data = load_data()
            save_welcome_message(data, "video", file_id, update.message.caption or update.message.document.file_name)
            save_data(data)
            context.user_data["welcome_message_mode"] = None
            await update.message.reply_text("✅ Welcome video saved.", reply_markup=admin_menu())
            return

    if not context.user_data.get("add_video_mode"):
        return

    file_id = None
    title = None

    if update.message.video:
        file_id = update.message.video.file_id
        title = update.message.caption or f"video_{update.message.video.file_unique_id}"
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/"):
        file_id = update.message.document.file_id
        title = update.message.caption or update.message.document.file_name or f"doc_{update.message.document.file_unique_id}"

    if not file_id:
        await update.message.reply_text("Please send a valid video file.")
        return

    data = load_data()
    videos = get_videos(data)
    videos.append(
        {
            "file_id": file_id,
            "title": title,
            "added_at": timestamp(),
        }
    )
    save_data(data)
    context.user_data["add_video_mode"] = False

    await update.message.reply_text(
        f"✅ Saved video #{len(videos)} successfully.\nTotal videos stored: {len(videos)}",
        reply_markup=admin_menu(),
    )


async def handle_admin_document_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("broadcast_mode") and update.message.document:
        file_id = update.message.document.file_id
        try:
            sent, failed = await execute_broadcast(context, content_type="document", file_id=file_id, caption=update.message.caption)
            await update.message.reply_text(
                f"✅ Broadcast done. Sent: {sent}, Failed: {failed}",
                reply_markup=admin_menu(),
            )
        finally:
            context.user_data.pop("broadcast_mode", None)
        return


async def execute_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    content_type: str,
    text: str | None = None,
    file_id: str | None = None,
    caption: str | None = None,
) -> tuple[int, int]:
    data = load_data()
    users = get_users(data)
    sent = 0
    failed = 0

    for user_id_str in list(users.keys()):
        try:
            chat_id = int(user_id_str)
        except Exception:
            failed += 1
            continue
            
        try:
            if content_type == "text" and text:
                await context.bot.send_message(chat_id=chat_id, text=text)
            elif content_type == "photo" and file_id:
                await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
            elif content_type == "video" and file_id:
                await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
            elif content_type == "animation" and file_id:
                await context.bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
            elif content_type == "document" and file_id:
                await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
            else:
                continue
            sent += 1
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", chat_id, exc)
            failed += 1
    
    return sent, failed


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if is_admin(user_id) and context.user_data.get("welcome_message_mode") == "add":
        data = load_data()
        save_welcome_message(data, "text", text)
        save_data(data)
        context.user_data["welcome_message_mode"] = None
        await update.message.reply_text(
            "✅ Welcome text saved.",
            reply_markup=admin_menu(),
        )
        return

    if is_admin(user_id) and context.user_data.get("welcome_message_mode") == "remove":
        data = load_data()
        messages = get_welcome_messages(data)
        if text.lower() in {"all", "clear", "reset"}:
            data["welcome_messages"] = []
            save_data(data)
            context.user_data["welcome_message_mode"] = None
            await update.message.reply_text(
                "🗑 All welcome messages removed.",
                reply_markup=admin_menu(),
            )
            return

        try:
            index = int(text)
        except ValueError:
            await update.message.reply_text("Please send a valid number, or ALL.")
            return

        if index < 1 or index > len(messages):
            await update.message.reply_text("That number is out of range.")
            return

        messages.pop(index - 1)
        data["welcome_messages"] = messages
        save_data(data)
        context.user_data["welcome_message_mode"] = None
        await update.message.reply_text(
            build_welcome_messages_text(data),
            reply_markup=admin_menu(),
        )
        return

    if is_admin(user_id) and context.user_data.get("buy_points_link_mode") == "set":
        data = load_data()
        link = normalize_buy_points_link(text)
        if not link:
            await update.message.reply_text("Please send a valid https:// link or @username.")
            return
        set_buy_points_link(data, link)
        save_data(data)
        context.user_data["buy_points_link_mode"] = None
        await update.message.reply_text(
            f"✅ Buy points link saved:\n{link}",
            reply_markup=admin_menu(),
        )
        return

    if is_admin(user_id) and context.user_data.get("forced_channels_remove_mode"):
        if text.lower() in {"back", "cancel"}:
            context.user_data["forced_channels_remove_mode"] = False
            await update.message.reply_text(
                "❌ Channel removal cancelled.",
                reply_markup=admin_menu(),
            )
            return

        try:
            index = int(text)
        except ValueError:
            await update.message.reply_text("Send a valid channel number.")
            return

        data = load_data()
        forced_channels = get_forced_channels(data)
        legacy_count = 1 if legacy_required_channel_entry() else 0
        visible_channels = get_all_required_channels(data)

        if index < 1 or index > len(visible_channels):
            await update.message.reply_text("Channel number out of range.")
            return

        real_index = index - 1 - legacy_count

        if real_index < 0:
            await update.message.reply_text("Legacy main channel cannot be removed.")
            return

        if real_index >= len(forced_channels):
            await update.message.reply_text("Channel number out of range.")
            return

        forced_channels.pop(real_index)
        data["forced_channels"] = forced_channels
        save_data(data)
        context.user_data["forced_channels_remove_mode"] = False

        await update.message.reply_text(
            build_forced_channels_text(data),
            reply_markup=build_forced_channels_keyboard(data),
        )
        return

    # Join channels add mode
    if is_admin(user_id) and context.user_data.get("forced_channels_add_mode"):
        if text.lower() in {"back", "cancel"}:
            context.user_data["forced_channels_add_mode"] = False
            await update.message.reply_text(
                "❌ Channel add cancelled.",
                reply_markup=admin_menu(),
            )
            return

        data = load_data()
        channels = get_all_required_channels(data)
        if len(channels) >= 5:
            context.user_data["forced_channels_add_mode"] = False
            await update.message.reply_text(
                "You already have the maximum of 5 join channels.",
                reply_markup=admin_menu(),
            )
            return

        channel, error = parse_forced_channel_input(text)
        if error:
            await update.message.reply_text(error)
            return

        assert channel is not None
        duplicate_keys = {str(ch.get("chat_id")) for ch in channels}
        if str(channel.get("chat_id")) in duplicate_keys:
            await update.message.reply_text(
                "This channel is already added. Send another one or type Back to cancel.",
            )
            return

        forced_channels = get_forced_channels(data)
        if len(forced_channels) + (1 if legacy_required_channel_entry() else 0) >= 5:
            context.user_data["forced_channels_add_mode"] = False
            await update.message.reply_text(
                "You already have the maximum of 5 join channels.",
                reply_markup=admin_menu(),
            )
            return

        forced_channels.append(channel)
        data["forced_channels"] = forced_channels
        save_data(data)
        context.user_data["forced_channels_add_mode"] = False

        await update.message.reply_text(
            build_forced_channels_text(data),
            reply_markup=build_forced_channels_keyboard(data),
        )
        return

    if is_admin(user_id):
        if text in {"🛠 Admin Dashboard", "Admin Dashboard"}:
            await admin_dashboard(update, context)
            return
        if text in {"🖼 Set Welcome Image", "Set Welcome Image"}:
            await set_welcome_image_command(update, context)
            return
        if text in {"➕ Add Video", "Add Video"}:
            await add_video_command(update, context)
            return
        if text in {"📋 List Videos", "List Videos"}:
            await list_videos_command(update, context)
            return
        if text in {"📢 Broadcast", "Broadcast"}:
            await broadcast_command(update, context)
            return
        if text in {"🔗 Join Channels", "Join Channels", "Join Channels"}:
            await show_forced_channels_panel(update, context)
            return
        if text == "➕ Add Welcome Message":
            await add_welcome_message_command(update, context)
            return
        if text == "🗑 Remove Welcome Message":
            await remove_welcome_message_command(update, context)
            return
        if text == "📋 List Welcome Messages":
            await list_welcome_messages_command(update, context)
            return
        if text == "💎 Set Buy Points Link":
            await set_buy_points_link_command(update, context)
            return
        if text == "👀 View Buy Points Link":
            await view_buy_points_link_command(update, context)
            return
        if text == "❌ Remove Buy Points Link":
            await remove_buy_points_link_command(update, context)
            return

    if is_admin(user_id) and context.user_data.get("broadcast_mode"):
        try:
            sent, failed = await execute_broadcast(context, content_type="text", text=text)
            await update.message.reply_text(
                f"✅ Broadcast done. Sent: {sent}, Failed: {failed}",
                reply_markup=admin_menu(),
            )
        finally:
            context.user_data.pop("broadcast_mode", None)
        return

    if text in {"🎬 GET VIDEO 🎬", "Get Video"}:
        await do_get_video(update, context)
        return
    if text in {"👤 Dashboard", "Dashboard"}:
        await do_dashboard(update, context)
        return
    if text in {"⭐ My Points", "My Points"}:
        await do_points(update, context)
        return
    if text in {"👥 Refer & Earn", "Refer & Earn"}:
        await do_refer(update, context)
        return
    if text == "🎁 Daily Bonus":
        await do_daily_bonus(update, context)
        return
    if text in {"❓ Help", "Help"}:
        await help_command(update, context)
        return
    if text == "💎 Buy Points":
        data = load_data()
        link = get_buy_points_link(data)
        if link:
            await update.message.reply_text(
                "💎 Buy Points",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Buy Points", url=link)]]),
            )
        else:
            await update.message.reply_text(
                "🚧 Coming Soon\nContact @jainsahiba18",
                reply_markup=menu_for_user(user_id),
            )
        return

# =====================
# APP
# =====================

async def daily_bonus_reminder_job(context):
    data = load_data()
    today = today_key()
    changed = False
    for uid, user in list(get_users(data).items()):
        try:
            if user.get("daily_bonus_date") != today and user.get("last_bonus_reminder") != today:
                await context.bot.send_message(
                    int(uid),
                    "🎁 Your Daily Bonus Is Ready!\n\nClaim your free point now.\n\n👥 Refer friends and earn more points.\n🎬 More points = More videos."
                )
                user["last_bonus_reminder"] = today
                changed = True
        except Exception as exc:
            logger.warning("Daily reminder failed for %s: %s", uid, exc)
    if changed:
        save_data(data)

async def inactive_user_reminder_job(context):
    data = load_data()
    changed = False
    for uid, user in list(get_users(data).items()):
        try:
            last = str(user.get("last_active_date") or "")
            if last:
                from datetime import date
                d = date.fromisoformat(last)
                today = now_local().date()
                if (today - d).days >= 3 and not user.get("inactive_reminder_sent"):
                    await context.bot.send_message(
                        int(uid),
                        "😎 We Miss You!\n\n🎁 Your daily bonus may be waiting.\n👥 Refer friends to earn more points."
                    )
                    user["inactive_reminder_sent"] = True
                    changed = True
        except Exception as exc:
            logger.warning("Inactive reminder failed for %s: %s", uid, exc)
    if changed:
        save_data(data)

async def post_init(application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("dashboard", "Dashboard"),
            BotCommand("getvideo", "Get video"),
            BotCommand("points", "My points"),
            BotCommand("help", "Help"),
        ]
    )

def build_app() -> Any:
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your .env file.")
    if not ADMIN_IDS:
        raise RuntimeError("Set ADMIN_IDS in your .env file.")
    if not REQUIRED_CHAT_ID:
        raise RuntimeError("Set REQUIRED_CHAT_ID in your .env file.")
    if not REQUIRED_CHAT_LINK and not (isinstance(REQUIRED_CHAT_ID, str) and REQUIRED_CHAT_ID.startswith("@")):
        logger.warning(
            "REQUIRED_CHAT_LINK is empty and REQUIRED_CHAT_ID is numeric. For private groups/channels, set REQUIRED_CHAT_LINK."
        )

    data = load_data()
    logger.info("Loaded %s videos", len(get_videos(data)))
    logger.info("Loaded %s users", len(get_users(data)))

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    from datetime import time
    if app.job_queue:
        app.job_queue.run_daily(
            daily_bonus_reminder_job,
            time=time(
                hour=10,
                minute=0,
                tzinfo=ZoneInfo(TIMEZONE_NAME),
            ),
            name="daily_bonus_reminder",
        )
        app.job_queue.run_daily(
            inactive_user_reminder_job,
            time=time(
                hour=18,
                minute=0,
                tzinfo=ZoneInfo(TIMEZONE_NAME),
            ),
            name="inactive_user_reminder",
        )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("points", points))
    app.add_handler(CommandHandler("getvideo", do_get_video))
    app.add_handler(CommandHandler("dailybonus", daily_bonus))
    app.add_handler(CommandHandler("refer", refer))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CommandHandler("addvideo", add_video_command))
    app.add_handler(CommandHandler("listvideos", list_videos_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("setwelcomeimage", set_welcome_image_command))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.ANIMATION, handle_admin_photo_upload))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_admin_video_upload))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.IMAGE & ~filters.Document.VIDEO, handle_admin_document_broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


if __name__ == "__main__":
    logger.info("Starting bot...")
    application = build_app()
    application.run_polling(allowed_updates=Update.ALL_TYPES)
