import os
import sys
import time
import subprocess
from telebot import TeleBot, types
from loguru import logger
import threading

from config_loader import config
from database import db
from actions import get_ram_usage_display, check_internet, get_tcp_streams, take_screenshot, run_root, get_battery_level
from monitor import watchdog

if not config.bot_token or config.bot_token == "YOUR_TOKEN":
    print("CRITICAL: BOT_TOKEN is missing in config.json! Please configure it before starting.")
    sys.exit(1)

bot = TeleBot(config.bot_token, parse_mode="HTML")
LOG_FILE = "farm_log.txt"

# Setup Logger
logger.add(LOG_FILE, rotation="10 MB", retention="3 days", level="INFO")

# Global UI State
ui_message_id = None
ui_chat_id = None
user_states = {} # chat_id -> state

def _bar(percent: float, length: int = 10) -> str:
    p = max(0.0, min(100.0, percent))
    fill = int((p / 100.0) * length)
    return "■" * fill + "□" * (length - fill)

def get_dashboard_text():
    ram_val = get_ram_usage_display()
    if isinstance(ram_val, (int, float)):
        ram_display = f"[{_bar(float(ram_val))}] {ram_val:.0f}%"
    else:
        ram_display = f"[{ram_val}]"

    con_status = "STABLE" if check_internet() else "OFFLINE"
    tcp_count = watchdog.current_tcp
    uptime = watchdog.get_uptime_str()
    battery = get_battery_level()
    status_text = watchdog.status
    
    active_link = db.get_active_link()
    srv_name = "NONE"
    if active_link:
        if "placeId=" in active_link:
            try:
                srv_name = active_link.split("placeId=")[1].split("&")[0]
            except:
                srv_name = "ROBLOX"
        else:
            srv_name = "ACTIVE_LINK"

    text = (
        f"<code>┏━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃  🦾 VEX_FARM :: {config.device_name}\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 🔋 RAM: {ram_display}\n"
        f"┃ 🌐 NET: {con_status} (TCP: {tcp_count})\n"
        f"┃ 🕒 UP: {uptime} | ⚡ {battery}%\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 🎮 STATUS: {status_text}\n"
        f"┃ 📍 SRV: {srv_name}\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━┛</code>"
    )
    return text

def main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ START", callback_data="start_farm"),
        types.InlineKeyboardButton("🛑 STOP", callback_data="ui:stop")
    )
    kb.add(
        types.InlineKeyboardButton("📸 SCREEN", callback_data="ui:screen"),
        types.InlineKeyboardButton("🔗 SET SERVER", callback_data="ui:set_server")
    )
    kb.add(types.InlineKeyboardButton("🔄 UPDATE", callback_data="ui:update"))
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
        bot.reply_to(message, "Usage: /exec [command]")
        return
    
    code, out, err = run_root(cmd)
    res = f"Code: {code}\nSTDOUT: {out}\nSTDERR: {err}"
    
    if len(res) > 4000:
        file_path = "exec_out.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(res)
        with open(file_path, "rb") as f:
            bot.send_document(message.chat.id, f)
        os.remove(file_path)
    else:
        bot.reply_to(message, f"<code>{res}</code>")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'waiting_for_link')
def handle_link_capture(message):
    if message.from_user.id not in config.admin_ids: return
    link = message.text.strip()
    if link.startswith("http"):
        if "privateServerLinkCode" not in link:
            bot.send_message(message.chat.id, "⚠️ <b>Warning:</b> This link missing <code>privateServerLinkCode</code>. It might only open the Game Home page.")
        
        db.add_link(link)
        links = db.get_links()
        db.set_active_link(links[-1][0])
        user_states[message.chat.id] = None
        bot.send_message(message.chat.id, "✅ Server link updated!")
        cmd_start(message)
    else:
        bot.send_message(message.chat.id, "❌ Invalid link. Process cancelled.")
        user_states[message.chat.id] = None

@bot.message_handler(commands=['update'])
def cmd_update(message):
    if message.from_user.id not in config.admin_ids: return
    update_system(message.chat.id)

def update_system(chat_id):
    bot.send_message(chat_id, "📟 <b>SYSTEM:</b> Nuclear update initiated...")
    
    script_content = f"""#!/bin/bash
sleep 2
pkill -9 python
cd ~/farm
git fetch origin
git reset --hard origin/main
git clean -fd
python main.py
"""
    with open("updater.sh", "w") as f:
        f.write(script_content)
    
    os.chmod("updater.sh", 0o755)
    subprocess.Popen(["/bin/bash", "./updater.sh"], start_new_session=True)
    sys.exit()

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    global ui_message_id, ui_chat_id, user_states
    if call.from_user.id not in config.admin_ids:
        bot.answer_callback_query(call.id, "Unauthorized")
        return

    try:
        if call.data == "start_farm":
            bot.answer_callback_query(call.id, "Starting Roblox...")
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
                
        elif call.data == "ui:set_server":
            bot.answer_callback_query(call.id)
            user_states[call.message.chat.id] = 'waiting_for_link'
            bot.send_message(call.message.chat.id, "🔗 Send me the new Roblox server link (Private or Game link):")
            
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
            pass 

def auto_updater():
    while True:
        time.sleep(10)
        update_ui()

if __name__ == "__main__":
    logger.info("Farm Watchdog Bot Started.")
    ui_thread = threading.Thread(target=auto_updater, daemon=True)
    ui_thread.start()
    bot.infinity_polling(timeout=60, long_polling_timeout=40)
