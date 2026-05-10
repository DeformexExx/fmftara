import subprocess
import shlex
import os
from loguru import logger

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
    # Requirement: start intent
    if not link:
        logger.error("No link provided to start_roblox")
        return False
    code, out, err = run_root(f"am start -a android.intent.action.VIEW -d '{link}' com.roblox.client")
    return code == 0

def stop_roblox():
    code, out, err = run_root("pkill -f com.roblox.client")
    return code == 0

def get_tcp_streams():
    code, out, err = run_root("cat /proc/net/tcp | wc -l")
    if code == 0 and out.isdigit():
        return int(out)
    return 0

def get_ram_usage():
    code, out, _ = run_root("cat /proc/meminfo")
    total_kb, free_kb = 1, 0
    if code == 0:
        for line in out.splitlines():
            if line.startswith("MemTotal:"): total_kb = int(line.split()[1])
            if line.startswith("MemAvailable:") or line.startswith("MemFree:"): free_kb = int(line.split()[1])
    if total_kb > 64*1024*1024: total_kb = 4*1024*1024 # Glitch Fix
    used_p = ((total_kb - free_kb) / total_kb) * 100
    return used_p

def check_internet():
    code, out, err = run_root("ping -c 1 8.8.8.8")
    return code == 0

def take_screenshot():
    sdcard_path = "/sdcard/screen.png"
    local_path = "screen.png"
    code, out, err = run_root(f"screencap -p {sdcard_path}")
    if code == 0:
        run_root(f"cp {sdcard_path} {local_path}")
        run_root(f"rm {sdcard_path}")
        return local_path
    return None
