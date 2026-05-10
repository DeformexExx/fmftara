import subprocess
import shlex
import os
import time
import re
from loguru import logger

# TCP Cache
_tcp_cache = {"count": 0, "time": 0}

def run_root(command: str) -> tuple[int, str, str]:
    """Execute command via su -c"""
    try:
        full_cmd = f'su -c {shlex.quote(command)}'
        process = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120
        )
        return process.returncode, process.stdout.strip(), process.stderr.strip()
    except Exception as e:
        logger.error(f"Root Shell Error: {e}")
        return -1, "", str(e)

def start_roblox(link):
    if not link:
        logger.error("No link provided to start_roblox")
        return False
    
    # Requirement: Parse to ensure it's a private server or game link
    if "privateServerLinkCode" not in link and "/games/" not in link:
        logger.warning("Link might be invalid for direct launch.")

    # Nuclear Launch Command with nohup for stability
    # Command: su -c "nohup am start -n com.roblox.client/.startup.ActivitySplash -a android.intent.action.VIEW -d '{link}' &"
    cmd = f"nohup am start -n com.roblox.client/.startup.ActivitySplash -a android.intent.action.VIEW -d '{link}' > /dev/null 2>&1 &"
    logger.info(f"Launching Roblox with nohup Intent: {cmd}")
    
    code, out, err = run_root(cmd)
    return code == 0

def stop_roblox():
    # Force kill
    code, out, err = run_root("pkill -9 com.roblox.client")
    return code == 0

def get_tcp_streams():
    global _tcp_cache
    now = time.time()
    # Cache TTL: 15s
    if now - _tcp_cache["time"] < 15:
        return _tcp_cache["count"]
    
    code, out, err = run_root("netstat -ntp | grep com.roblox.client | wc -l")
    if code == 0 and out.isdigit():
        _tcp_cache["count"] = int(out)
        _tcp_cache["time"] = now
        return _tcp_cache["count"]
    return 0

def get_ram_usage_display():
    """Return percent (float) or string for display."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        # Check for exabyte bug (e.g. total > 1TB)
        if mem.total > 1024 * 1024 * 1024 * 1024:
            return "Shared/System"
        return mem.percent
    except:
        return "Shared/System"

def get_battery_level():
    code, out, _ = run_root("dumpsys battery | grep level")
    if code == 0 and out:
        try:
            return int(out.split(":")[1].strip())
        except:
            return 0
    return 0

def check_internet():
    code, out, err = run_root("ping -c 1 8.8.8.8")
    return code == 0

def take_screenshot():
    tmp_path = "/data/local/tmp/s.png"
    local_path = "screen.png"
    cmd = f"screencap -p {tmp_path} && chmod 777 {tmp_path}"
    code, out, err = run_root(cmd)
    if code == 0:
        run_root(f"cp {tmp_path} {os.getcwd()}/{local_path}")
        run_root(f"rm {tmp_path}")
        return local_path
    return None
