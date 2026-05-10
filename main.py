import os
import sys
import time
import subprocess
from telebot import TeleBot, types
from loguru import logger
import threading

from config_loader import config
from database import db
from actions import get_ram_usage, check_internet, get_tcp_streams, take_screenshot, run_root
from monitor import watchdog

if not config.bot_token or config.bot_token == "YOUR_TOKEN":
    print("CRITICAL: BOT_TOKEN is missing in config.json! Please configure it before starting.")
    sys.exit(1)

bot = TeleBot(config.bot_token, parse_mode="HTML")
LOG_FILE = "farm_log.txt"

# Setup Logger
logger.add(LOG_FILE, rotation="10 MB", retention="3 days", level="INFO")

# Global UI Message ID
ui_message_id = None
ui_chat_id = None

def _bar(percent: float, length: int = 10) -> str:
    p = max(0.0, min(100.0, percent))
    fill = int((p / 100.0) * length)
    return "■" * fill + "□" * (length - fill)

def get_dashboard_text():
    used_p = get_ram_usage()
    con_status = "Stable" if check_internet() else "Offline"
    tcp_count = get_tcp_streams()
    uptime = watchdog.get_uptime_str()
    
    text = (
        f"📟 <b>DEVICE: {config.device_name}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔋 RAM: [{_bar(used_p)}] {used_p:.0f}%\n"
        f"🌐 CON: {con_status}\n"
        f"🧊 TCP Streams: {tcp_count}\n"
        f"🕒 Uptime: {uptime}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 Status: {watchdog.status}\n"
    )
    return text

def main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    btn_start = types.InlineKeyboardButton("▶️ START", callback_data="ui:start")
    btn_stop = types.InlineKeyboardButton("🛑 STOP", callback_data="ui:stop")
    btn_screen = types.InlineKeyboardButton("📸 SCREENSHOT", callback_data="ui:screen")
    btn_add_server = types.InlineKeyboardButton("➕ ADD SERVER", callback_data="ui:add_server")
    btn_update = types.InlineKeyboardButton("🔄 UPDATE", callback_data="ui:update")
    btn_stats = types.InlineKeyboardButton("📊 STATS", callback_data="ui:stats")
    
    kb.add(btn_start, btn_stop)
    kb.add(btn_screen, btn_add_server)
    kb.add(btn_update, btn_stats)
    return kb

@bot.message_handler(commands=['start', 'menu'])
def cmd_start(message):
    global ui_message_id, ui_chat_id
    if message.from_user.id not in config.admin_ids: return
    
    msg = bot.send_message(message.chat.id, get_dashboard_text(), reply_markup=main_keyboard())
    ui_message_id = msg.message_id
    ui_chat_id = msg.chat.id

@bot.message_handler(commands=['exec'])
def cmd_exec(message):
    if message.from_user.id not in config.admin_ids: return
    cmd = message.text.replace("/exec", "", 1).strip()
    if not cmd:
        bot.reply_to(message, "Usage: /exec <command>")
        return
    
    code, out, err = run_root(cmd)
    res = f"Code: {code}\nSTDOUT: {out}\nSTDERR: {err}"
    
    if len(res) > 4000:
        with open("exec_out.txt", "w", encoding="utf-8") as f:
            f.write(res)
        with open("exec_out.txt", "rb") as f:
            bot.send_document(message.chat.id, f)
        os.remove("exec_out.txt")
    else:
        bot.reply_to(message, f"```\n{res}\n```", parse_mode="Markdown")

@bot.message_handler(commands=['update'])
def cmd_update(message):
    if message.from_user.id not in config.admin_ids: return
    update_system(message.chat.id)

def update_system(chat_id):
    bot.send_message(chat_id, "🔄 Updating from git...")
    if config.git_repo_url:
        run_root(f"git remote set-url origin {config.git_repo_url}")
    run_root("git fetch --all")
    run_root("git reset --hard origin/main")
    run_root("git pull")
    
    bot.send_message(chat_id, "✅ Update complete. Restarting bot...")
    logger.info("Bot is restarting for update.")
    os.execv(sys.executable, ['python'] + sys.argv)

def process_add_server(message):
    if message.from_user.id not in config.admin_ids: return
    link = message.text.strip()
    if link.startswith("http"):
        db.add_link(link)
        # Auto set as active if it's the only one
        links = db.get_links()
        if len(links) == 1:
            db.set_active_link(links[0][0])
        bot.send_message(message.chat.id, "✅ Server added and set as active!")
    else:
        bot.send_message(message.chat.id, "❌ Invalid link.")
    
    cmd_start(message)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    global ui_message_id, ui_chat_id
    if call.from_user.id not in config.admin_ids:
        bot.answer_callback_query(call.id, "Unauthorized")
        return

    try:
        if call.data == "ui:start":
            bot.answer_callback_query(call.id, "Starting Watchdog...")
            watchdog.start()
            update_ui()
            
        elif call.data == "ui:stop":
            bot.answer_callback_query(call.id, "Stopping Watchdog...")
            watchdog.stop()
            update_ui()
            
        elif call.data == "ui:screen":
            bot.answer_callback_query(call.id, "Capturing screenshot...")
            path = take_screenshot()
            if path and os.path.exists(path):
                with open(path, 'rb') as f:
                    bot.send_photo(call.message.chat.id, f, caption="📸 Screenshot")
                os.remove(path)
            else:
                bot.send_message(call.message.chat.id, "❌ Failed to take screenshot.")
                
        elif call.data == "ui:add_server":
            bot.answer_callback_query(call.id)
            msg = bot.send_message(call.message.chat.id, "Send Roblox Server Link:")
            bot.register_next_step_handler(msg, process_add_server)
            
        elif call.data == "ui:stats":
            bot.answer_callback_query(call.id, "Refreshing stats...")
            update_ui()
            
        elif call.data == "ui:update":
            bot.answer_callback_query(call.id)
            update_system(call.message.chat.id)

    except Exception as e:
        logger.error(f"Callback Error: {e}")

def update_ui():
    global ui_message_id, ui_chat_id
    if ui_message_id and ui_chat_id:
        try:
            bot.edit_message_text(get_dashboard_text(), ui_chat_id, ui_message_id, reply_markup=main_keyboard())
        except Exception as e:
            pass # Ignore unchanged message error

def auto_updater():
    while True:
        time.sleep(10)
        update_ui()

if __name__ == "__main__":
    logger.info("Farm Watchdog Bot Started.")
    ui_thread = threading.Thread(target=auto_updater, daemon=True)
    ui_thread.start()
    
    bot.infinity_polling(timeout=60, long_polling_timeout=40)
