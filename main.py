import os
import sys
import time
import subprocess
from telebot import TeleBot, types
from loguru import logger
import threading

from config_loader import config
from database import db
from actions import get_ram_usage, check_internet, get_tcp_streams, take_screenshot, run_root, get_battery_level, extract_link_code, get_bypass_link, update_license
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
    return "|" * fill + "-" * (length - fill)

def get_dashboard_text():
    used_p = get_ram_usage()
    con_status = "STABLE" if check_internet() else "OFFLINE"
    tcp_count, tcp_status_text = watchdog.current_tcp, watchdog.tcp_status
    uptime = watchdog.get_uptime_str()
    battery = get_battery_level()
    status_text = watchdog.status
    
    active_code = db.get_active_link()
    srv_display = active_code if active_code else "NONE"
    if len(srv_display) > 15:
        srv_display = srv_display[:12] + "..."

    text = (
        f"<code>┏━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃  🦾 VEX_FARM :: {config.device_name}\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 🔋 RAM: [{_bar(used_p)}] {used_p:.0f}%\n"
        f"┃ 🌐 NET: {con_status} ({tcp_status_text})\n"
        f"┃ 🕒 UP: {uptime} | ⚡ {battery}%\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 🎮 STATUS: {status_text}\n"
        f"┃ 📍 SRV: {srv_display}\n"
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
        types.InlineKeyboardButton("🔗 GET BYPASS LINK", callback_data="ui:get_bypass"),
        types.InlineKeyboardButton("🔑 UPDATE LICENSE", callback_data="ui:set_license")
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

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) in ['waiting_for_link', 'waiting_for_license'])
def handle_state_inputs(message):
    if message.from_user.id not in config.admin_ids: return
    state = user_states.get(message.chat.id)
    text = message.text.strip()
    
    if state == 'waiting_for_link':
        if text.startswith("http"):
            link_code = extract_link_code(text)
            db.add_link(link_code)
            links = db.get_links()
            db.set_active_link(links[-1][0])
            user_states[message.chat.id] = None
            bot.send_message(message.chat.id, f"✅ LinkCode captured: <code>{link_code}</code>")
            cmd_start(message)
        else:
            bot.send_message(message.chat.id, "❌ Invalid link. Process cancelled.")
            user_states[message.chat.id] = None

    elif state == 'waiting_for_license':
        if update_license(text):
            user_states[message.chat.id] = None
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⚡ RESTART ROBLOX", callback_data="ui:restart_manual"))
            bot.send_message(message.chat.id, "✅ <b>License updated successfully!</b>\nRestart Roblox to apply changes.", reply_markup=kb)
        else:
            bot.send_message(message.chat.id, "❌ Failed to update license file.")
            user_states[message.chat.id] = None

@bot.message_handler(commands=['update'])
def cmd_update(message):
    if message.from_user.id not in config.admin_ids: return
    update_system(message.chat.id)

def update_system(chat_id):
    bot.send_message(chat_id, "📟 <b>SYSTEM:</b> Executing Nuclear Update. Cleaning environment...")
    
    script_content = f"""#!/system/bin/sh
sleep 3
pkill -9 python
cd ~/farm
git fetch origin
git reset --hard origin/main
git clean -fd
pip install psutil
python main.py
"""
    with open("reboot_farm.sh", "w") as f:
        f.write(script_content)
    
    os.chmod("reboot_farm.sh", 0o755)
    subprocess.Popen(['sh', 'reboot_farm.sh'], start_new_session=True)
    sys.exit()

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    global ui_message_id, ui_chat_id, user_states
    if call.from_user.id not in config.admin_ids:
        bot.answer_callback_query(call.id, "Unauthorized")
        return

    try:
        if call.data == "start_farm":
            bot.answer_callback_query(call.id, "Starting Watchdog Engine...")
            watchdog.start()
            update_ui()
            
        elif call.data == "ui:stop":
            bot.answer_callback_query(call.id, "Halting Watchdog Engine...")
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

        elif call.data == "ui:get_bypass":
            bot.answer_callback_query(call.id)
            url = get_bypass_link()
            if url:
                bot.send_message(call.message.chat.id, f"🔗 <b>Your Bypass Link:</b>\n<code>{url}</code>")
            else:
                bot.send_message(call.message.chat.id, "❌ Link not found in system recents.\nPlease click 'Get Key' in Roblox first.")

        elif call.data == "ui:set_license":
            bot.answer_callback_query(call.id)
            user_states[call.message.chat.id] = 'waiting_for_license'
            bot.send_message(call.message.chat.id, "📟 <b>System ready.</b>\nPlease send the new key (<code>FREE_...</code>):")

        elif call.data == "ui:restart_manual":
            bot.answer_callback_query(call.id, "Restarting Roblox...")
            watchdog.restart_sequence()
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
            # If edit fails (message deleted), send a NEW one
            try:
                msg = bot.send_message(ui_chat_id, get_dashboard_text(), reply_markup=main_keyboard())
                ui_message_id = msg.message_id
            except:
                pass 

def auto_updater():
    while True:
        time.sleep(10)
        update_ui()

if __name__ == "__main__":
    logger.info("Farm Watchdog Bot Started.")
    # Bulletproof: start daemon thread immediately
    ui_thread = threading.Thread(target=auto_updater, daemon=True)
    ui_thread.start()
    
    # Also ensure watchdog loop is running background
    if not watchdog.thread or not watchdog.thread.is_alive():
        watchdog.thread = threading.Thread(target=watchdog._loop, daemon=True)
        watchdog.thread.start()

    bot.infinity_polling(timeout=60, long_polling_timeout=40)
