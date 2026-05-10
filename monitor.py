import threading
import time
from loguru import logger
from actions import get_tcp_streams, stop_roblox, start_roblox
from database import db

class Watchdog:
    def __init__(self):
        self.running = False
        self.status = "IDLE"
        self.thread = None
        self.tcp_failures = 0
        self.start_time = None
        
    def get_uptime_str(self):
        if not self.start_time or self.status != "RUNNING":
            return "00:00:00"
        delta = int(time.time() - self.start_time)
        hours, rem = divmod(delta, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def start(self):
        if not self.running:
            self.running = True
            self.status = "RUNNING"
            self.start_time = time.time()
            self.tcp_failures = 0
            
            link = db.get_active_link()
            if link:
                start_roblox(link)
                
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            logger.info("Watchdog started.")

    def stop(self):
        if self.running:
            self.running = False
            self.status = "IDLE"
            self.start_time = None
            stop_roblox()
            logger.info("Watchdog stopped.")

    def _loop(self):
        while self.running:
            try:
                tcp_count = get_tcp_streams()
                
                # Logic: If TCP count < 4 for 3 consecutive checks (approx. 30s), trigger RESTART
                if tcp_count < 4:
                    self.tcp_failures += 1
                    logger.warning(f"TCP Streams low: {tcp_count}. Failure count: {self.tcp_failures}/3")
                else:
                    self.tcp_failures = 0
                
                if self.tcp_failures >= 3:
                    logger.error("Watchdog triggered restart! TCP streams < 4 for 3 checks.")
                    self.restart_sequence()
                    self.tcp_failures = 0
                
                time.sleep(10)
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                time.sleep(10)

    def restart_sequence(self):
        self.status = "RESTARTING"
        logger.info("Restart Sequence: Stopping Roblox...")
        stop_roblox()
        time.sleep(5)
        link = db.get_active_link()
        if link:
            logger.info(f"Restart Sequence: Starting Roblox with active link...")
            start_roblox(link)
        else:
            logger.warning("Restart Sequence: No active link found!")
        self.status = "RUNNING"
        self.start_time = time.time()

watchdog = Watchdog()
