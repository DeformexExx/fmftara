import sqlite3
import os
from loguru import logger

class Database:
    def __init__(self, db_file="farm.db"):
        self.db_file = db_file
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS server_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link TEXT NOT NULL,
                    active INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def add_link(self, link):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                conn.execute("INSERT INTO server_links (link) VALUES (?)", (link,))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding link: {e}")
            return False

    def get_links(self):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, link, active FROM server_links")
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting links: {e}")
            return []

    def set_active_link(self, link_id):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                conn.execute("UPDATE server_links SET active = 0")
                conn.execute("UPDATE server_links SET active = 1 WHERE id = ?", (link_id,))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error setting active link: {e}")
            return False

    def get_active_link(self):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT link FROM server_links WHERE active = 1")
                res = cursor.fetchone()
                if res:
                    return res[0]
                return None
        except Exception as e:
            logger.error(f"Error getting active link: {e}")
            return None

    def delete_link(self, link_id):
        try:
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                conn.execute("DELETE FROM server_links WHERE id = ?", (link_id,))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting link: {e}")
            return False

db = Database()
