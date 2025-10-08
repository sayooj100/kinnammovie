import telebot
from telebot import types
import random
import string
import http.server
import socketserver
import threading
import time
import os

# --- MongoDB connection ---
from pymongo import MongoClient

# Use environment variables for security
API_TOKEN = os.getenv("API_TOKEN")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://sayoojsayoojks72_db_user:VhwhDjdntcMQwnjW@telegrambot.ya7hmql.mongodb.net/?retryWrites=true&w=majority&appName=telegrambot")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID"))
FIXED_CHANNEL_1 = "@kinnammovie"

try:
    client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
    db = client["telegram_bot"]
    channels_col = db["private_channels"]
    batches_col = db["batches"]
    print("✅ MongoDB connected")
except Exception as e:
    print("❌ MongoDB connection failed:", e)

bot = telebot.TeleBot(API_TOKEN)

# Configure timeouts for better stability
import telebot.apihelper
telebot.apihelper.READ_TIMEOUT = 60
telebot.apihelper.CONNECT_TIMEOUT = 30

# ---------------- DATA STRUCTURES ----------------
private_channels = {}
files_db = {}
pending_batches = {}

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

        channels_col.update_one(
            {"admin_id": admin_id},
            {"$set": {"chat_id": chat_id, "invite_link": invite_link}},
            upsert=True
        )

        private_channels[admin_id] = {"chat_id": chat_id, "invite_link": invite_link}

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
        try:
            forwarded = bot.forward_message(STORAGE_GROUP_ID, admin_id, message.message_id)
            pending_batches[admin_id]["files"].append(forwarded.message_id)
            bot.send_message(admin_id, f"✅ File added ({len(pending_batches[admin_id]['files'])} so far)")
        except Exception as e:
            bot.send_message(admin_id, f"❌ Error forwarding file: {e}")

@bot.message_handler(commands=['done'])
def finalize_batch(message):
    admin_id = message.chat.id
    if admin_id in pending_batches and pending_batches[admin_id]["files"]:
        batch = pending_batches[admin_id]

        batches_col.insert_one({
            "code": batch["code"],
            "admin_id": admin_id,
            "files": batch["files"]
        })

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

    batch = files_db.get(code)

    if not batch:
        db_batch = batches_col.find_one({"code": code})
        if db_batch:
            batch = {"files": db_batch["files"], "admin_id": db_batch["admin_id"]}
            files_db[code] = batch
        else:
            bot.send_message(user_id, "❌ Invalid or expired link.")
            return

    admin_id = batch["admin_id"]
    ch1 = FIXED_CHANNEL_1

    ch2 = private_channels.get(admin_id)

    if not ch2:
        db_ch = channels_col.find_one({"admin_id": admin_id})
        if db_ch:
            ch2 = {"chat_id": db_ch["chat_id"], "invite_link": db_ch["invite_link"]}
            private_channels[admin_id] = ch2
        else:
            bot.send_message(user_id, "❌ Channel information not found.")
            return

    try:
        m1 = bot.get_chat_member(ch1, user_id)
        m2 = bot.get_chat_member(ch2["chat_id"], user_id)

        if (m1.status in ["member", "administrator", "creator"]) and \
           (m2.status in ["member", "administrator", "creator"]):
            for msg_id in batch["files"]:
                try:
                    bot.copy_message(user_id, STORAGE_GROUP_ID, msg_id)
                except Exception as e:
                    print(f"Error copying message: {e}")
        else:
            ask_to_join(user_id, ch1, ch2["invite_link"], code)
    except Exception as e:
        print(f"Membership check error: {e}")
        ask_to_join(user_id, ch1, ch2["invite_link"], code)

def ask_to_join(user_id, ch1, invite_link, code):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("✅ Join Channel 1", url=f"https://t.me/{ch1.strip('@')}")
    btn2 = types.InlineKeyboardButton("✅ Join Channel 2", url=invite_link)
    retry_btn = types.InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot.get_me().username}?start={code}")
    markup.add(btn1, btn2, retry_btn)
    bot.send_message(user_id, "⚠️ Join both channels to access files.", reply_markup=markup)

# ---------------- HEALTH CHECK SERVER ----------------
class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is running!')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress normal HTTP logging
        return

def run_health_server():
    port = 8080
    try:
        with socketserver.TCPServer(('', port), HealthHandler) as httpd:
            print(f"✅ Health check server running on port {port}")
            httpd.serve_forever()
    except Exception as e:
        print(f"❌ Health server error: {e}")

# ---------------- BOT POLLING WITH ERROR HANDLING ----------------
def start_bot():
    while True:
        try:
            print(" Starting bot polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60, restart_on_change=True)
        except Exception as e:
            print(f"❌ Bot polling error: {e}")
            print("🔄 Restarting in 10 seconds...")
            time.sleep(10)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print("🚀 Initializing Kinnam Movie Bot...")
    
    # Start health server in a separate thread
    health_thread = threading.Thread(target=run_health_server)
    health_thread.daemon = True
    health_thread.start()
    
    # Give health server a moment to start
    time.sleep(2)
    
    # Start the bot
    start_bot()
