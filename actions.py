import subprocess
import shlex
import os
import time
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
    # Use direct intent for com.roblox.client
    code, out, err = run_root(f"am start -a android.intent.action.VIEW -d '{link}' com.roblox.client")
    return code == 0

def stop_roblox():
    # Kill using pkill
    code, out, err = run_root("pkill com.roblox.client")
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

def get_ram_usage():
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        # Fallback for abnormal meminfo: assume 4GB total
        code, out, _ = run_root("cat /proc/meminfo")
        if code == 0:
            free_kb = 0
            for line in out.splitlines():
                if line.startswith("MemAvailable:") or line.startswith("MemFree:"): 
                    free_kb = int(line.split()[1])
                    break
            total_kb = 4 * 1024 * 1024
            used_kb = total_kb - free_kb
            return (used_kb / total_kb) * 100
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
    # Execute as: su -c "screencap -p /data/local/tmp/s.png && chmod 777 /data/local/tmp/s.png"
    cmd = f"screencap -p {tmp_path} && chmod 777 {tmp_path}"
    code, out, err = run_root(cmd)
    if code == 0:
        # Copy to local dir to ensure accessibility
        run_root(f"cp {tmp_path} {os.getcwd()}/{local_path}")
        run_root(f"rm {tmp_path}")
        return local_path
    return None
