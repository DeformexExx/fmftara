import threading
import time
from loguru import logger
from actions import get_tcp_streams_data, stop_roblox, start_roblox, is_roblox_running
from database import db

class Watchdog:
    def __init__(self):
        self.running = False
        self.status = "IDLE"
        self.thread = None
        self.tcp_failures = 0
        self.start_time = None
        self.current_tcp = 0
        self.tcp_status = "IDLE"
        
    def get_uptime_str(self):
        if not self.start_time or self.status == "IDLE":
            return "00:00:00"
        delta = int(time.time() - self.start_time)
        hours, rem = divmod(delta, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def start(self):
        self.running = True
        self.status = "ACTIVE"
        if not self.start_time:
            self.start_time = time.time()
        self.tcp_failures = 0
        
        code = db.get_active_link()
        if code:
            start_roblox(code)
            
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        logger.info("Watchdog Engine initialized.")

    def stop(self):
        self.running = False
        self.status = "STOPPED"
        self.start_time = None
        stop_roblox()
        logger.info("Watchdog Engine halted.")

    def _loop(self):
        while True:
            if not self.running:
                time.sleep(5)
                continue
                
            try:
                self.current_tcp, self.tcp_status = get_tcp_streams_data()
                is_running = is_roblox_running()
                
                # Aegis Engine Logic:
                # If running but TCP <= 3 -> ZOMBIE
                if is_running and self.current_tcp <= 3:
                    self.tcp_failures += 1
                    logger.warning(f"⚠️ Watchdog: Connection lost (ZOMBIE state). Failure: {self.tcp_failures}/3")
                else:
                    self.tcp_failures = 0
                
                if self.tcp_failures >= 3:
                    logger.error("🛑 Aegis Engine: ZOMBIE detected. Executing Force Restart...")
                    self.restart_sequence()
                    self.tcp_failures = 0
                
                time.sleep(15) # 15s cycle
            except Exception as e:
                logger.error(f"Watchdog Loop Error: {e}")
                time.sleep(15)

    def restart_sequence(self):
        self.status = "RESTARTING"
        stop_roblox() # am force-stop
        time.sleep(2)
        code = db.get_active_link()
        if code:
            start_roblox(code)
        self.status = "ACTIVE"
        self.start_time = time.time()

watchdog = Watchdog()
