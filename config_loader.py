import json
import os
from loguru import logger

CONFIG_FILE = "config.json"

class Config:
    def __init__(self):
        self.device_name = "DEV_1"
        self.bot_token = ""
        self.admin_ids = []
        self.git_repo_url = ""
        self.load()

    def load(self):
        try:
            if not os.path.exists(CONFIG_FILE):
                logger.warning(f"{CONFIG_FILE} not found. Creating default.")
                self.save()
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.device_name = data.get("DEVICE_NAME", "DEV_1")
                self.bot_token = data.get("BOT_TOKEN", "")
                self.admin_ids = data.get("ADMIN_IDS", [])
                self.git_repo_url = data.get("GIT_REPO_URL", "")
        except Exception as e:
            logger.error(f"Error loading config: {e}")

    def save(self):
        try:
            data = {
                "DEVICE_NAME": self.device_name,
                "BOT_TOKEN": self.bot_token,
                "ADMIN_IDS": self.admin_ids,
                "GIT_REPO_URL": self.git_repo_url
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {e}")

config = Config()
