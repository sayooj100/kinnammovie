import telebot
from telebot import types
import random
import string


# --- MongoDB connection (Step 1) ---
from pymongo import MongoClient
import os

# put your actual URI here (or load from env: os.getenv("MONGO_URI"))
MONGO_URI = "mongodb+srv://sayoojsayoojks72_db_user:VhwhDjdntcMQwnjW@telegrambot.ya7hmql.mongodb.net/?retryWrites=true&w=majority&appName=telegrambot"

try:
    client = MongoClient(MONGO_URI, tls=True)
    db = client["telegram_bot"]            # database
    channels_col = db["private_channels"]  # store { admin_id, chat_id, invite_link }
    batches_col = db["batches"]            # store { code, admin_id, files: [...], created_at }
    print(" MongoDB connected")
except Exception as e:
    print(" MongoDB connection failed:", e)
    # optionally exit or continue with in-memory mode


API_TOKEN = os.getenv("API_TOKEN")              # Telegram bot token
MONGO_URI = os.getenv("MONGO_URI")              # MongoDB connection string
ADMIN_ID = int(os.getenv("ADMIN_ID"))           # Your Telegram ID
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID"))
FIXED_CHANNEL_1 = "@kinnammovie"

bot = telebot.TeleBot(API_TOKEN)

# ---------------- DATA STRUCTURES ----------------
private_channels = {}     # {admin_id: {"chat_id": int, "invite_link": str}}
files_db = {}            # {random_code: {"files": [message_ids], "admin_id": id}}
pending_batches = {}     # {admin_id: {"code": str, "files": [], "invite_link": str, "chat_id": int}}

# ---------------- START HANDLER ----------------
@bot.message_handler(commands=['start'])
def start_command(message):
    if message.chat.id == ADMIN_ID:
        bot.send_message(message.chat.id, "Send the invite link of your private channel (bot must be a member/admin).")
        bot.register_next_step_handler(message, ask_private_channel_link)
    else:
        args = message.text.split()
        if len(args) > 1:
            code = args[1]
            handle_user_request(message, code)
        else:
            bot.send_message(message.chat.id, "Hello! Please use a valid link to access files.")

# ---------------- ADMIN FLOW ----------------
def ask_private_channel_link(message):
    admin_id = message.chat.id
    pending_batches[admin_id] = {"invite_link": message.text.strip()}
    bot.send_message(admin_id, "Now send the **numeric chat ID** of this private channel:")
    bot.register_next_step_handler(message, save_private_channel_id)

def save_private_channel_id(message):
    admin_id = message.chat.id
    try:
        chat_id = int(message.text.strip())
        invite_link = pending_batches[admin_id]["invite_link"]

        # Save to MongoDB (overwrite old if exists)
        channels_col.update_one(
            {"admin_id": admin_id},
            {"$set": {"chat_id": chat_id, "invite_link": invite_link}},
            upsert=True
        )

        # Keep in memory too (optional for fast access)
        private_channels[admin_id] = {"chat_id": chat_id, "invite_link": invite_link}

        # Start new batch
        code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        pending_batches[admin_id].update({"code": code, "files": [], "chat_id": chat_id})
        bot.send_message(admin_id,
            f"✅ Private channel saved!\nNow send me files (documents, videos, photos).\n"
            f"When done, type /done to generate your access link."
        )
    except:
        bot.send_message(admin_id, "❌ Invalid chat ID. Send numeric chat ID.")


@bot.message_handler(content_types=["document", "video", "photo"])
def collect_files(message):
    admin_id = message.chat.id
    if admin_id in pending_batches:
        forwarded = bot.forward_message(STORAGE_GROUP_ID, admin_id, message.message_id)
        pending_batches[admin_id]["files"].append(forwarded.message_id)
        bot.send_message(admin_id, f"✅ File added ({len(pending_batches[admin_id]['files'])} so far)")

@bot.message_handler(commands=['done'])
def finalize_batch(message):
    admin_id = message.chat.id
    if admin_id in pending_batches and pending_batches[admin_id]["files"]:
        batch = pending_batches[admin_id]

        # Save batch in MongoDB
        batches_col.insert_one({
            "code": batch["code"],
            "admin_id": admin_id,
            "files": batch["files"]
        })

        # Also keep in memory (optional, for fast access)
        files_db[batch["code"]] = {"files": batch["files"], "admin_id": admin_id}

        del pending_batches[admin_id]
        bot.send_message(admin_id,
            f"✅ Files saved!\nHere is your unique link:\nhttps://t.me/{bot.get_me().username}?start={batch['code']}"
        )
    else:
        bot.send_message(admin_id, "❌ No files added. Send files first.")


# ---------------- USER FLOW ----------------
def handle_user_request(message, code):
    user_id = message.chat.id

    # --- First try memory ---
    batch = files_db.get(code)

    # --- If not in memory, try MongoDB ---
    if not batch:
        db_batch = batches_col.find_one({"code": code})
        if db_batch:
            batch = {"files": db_batch["files"], "admin_id": db_batch["admin_id"]}
            files_db[code] = batch  # cache it in memory
        else:
            bot.send_message(user_id, "❌ Invalid or expired link.")
            return

    admin_id = batch["admin_id"]
    ch1 = FIXED_CHANNEL_1

    # --- Try memory first for channel ---
    ch2 = private_channels.get(admin_id)

    # --- If not in memory, try MongoDB ---
    if not ch2:
        db_ch = channels_col.find_one({"admin_id": admin_id})
        if db_ch:
            ch2 = {"chat_id": db_ch["chat_id"], "invite_link": db_ch["invite_link"]}
            private_channels[admin_id] = ch2  # cache
        else:
            bot.send_message(user_id, "❌ Channel information not found.")
            return

    try:
        # Check membership in both channels
        m1 = bot.get_chat_member(ch1, user_id)
        m2 = bot.get_chat_member(ch2["chat_id"], user_id)

        if (m1.status in ["member", "administrator", "creator"]) and \
           (m2.status in ["member", "administrator", "creator"]):
            for msg_id in batch["files"]:
                bot.copy_message(user_id, STORAGE_GROUP_ID, msg_id)
        else:
            ask_to_join(user_id, ch1, ch2["invite_link"], code)
    except:
        ask_to_join(user_id, ch1, ch2["invite_link"], code)

def ask_to_join(user_id, ch1, invite_link, code):
    markup = types.InlineKeyboardMarkup(row_width=1)

    # Button for fixed public channel
    btn1 = types.InlineKeyboardButton("✅ Join Channel 1", url=f"https://t.me/{ch1.strip('@')}")
    # Button for private channel using admin-provided invite link
    btn2 = types.InlineKeyboardButton("✅ Join Channel 2", url=invite_link)
    # Retry button (link to same /start=code)
    retry_btn = types.InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot.get_me().username}?start={code}")

    # Add buttons vertically
    markup.add(btn1)
    markup.add(btn2)
    markup.add(retry_btn)

    bot.send_message(user_id, "⚠️ Join both channels to access files.", reply_markup=markup)

# ---------------- RUN BOT ----------------
print("Bot is running...")
bot.infinity_polling()





