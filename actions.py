import subprocess
import shlex
import os
import time
import re
from loguru import logger

# TCP Cache
_tcp_cache = {"count": 0, "time": 0}
ADOPT_ME_ID = "920587237"

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

def extract_link_code(link: str) -> str:
    """Extract ONLY the value after privateServerLinkCode="""
    match = re.search(r"privateServerLinkCode=([\w-]+)", link)
    if match:
        return match.group(1)
    return link # Return as is if no code found

def start_roblox(link_code):
    if not link_code:
        logger.error("No link_code provided to start_roblox")
        return False
    
    # Golden Link Protocol
    formatted_link = f"roblox://placeID={ADOPT_ME_ID}&linkCode={link_code}"
    logger.info(f"Launching Adopt Me with Code: {link_code}")
    
    # Execute intent
    cmd = f"am start -a android.intent.action.VIEW -d '{formatted_link}' com.roblox.client"
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
    
    # Requirement: su -c "cat /proc/net/tcp | wc -l"
    code, out, err = run_root("cat /proc/net/tcp | wc -l")
    if code == 0 and out.isdigit():
        _tcp_cache["count"] = int(out)
        _tcp_cache["time"] = now
        return _tcp_cache["count"]
    return 0

def get_ram_usage():
    """Return percent (float)."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except:
        return 0.0

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
