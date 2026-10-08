"""Actions Jarvis can perform on the PC."""

import ctypes
import difflib
import re
import datetime
import glob
import json
import os
import urllib.request
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import psutil
import pyautogui
import pyperclip

# Set by jarvis.py: confirm(question) -> bool, and say(text) for reminders.
confirm = lambda question, details=None: False
ask = lambda question: ""
say = print

HOME = Path.home()


def _resolve(path):
    """Expand ~ and fall back to OneDrive-redirected folders (Desktop, Documents, Pictures)."""
    p = Path(os.path.expanduser(path))
    if not p.exists():
        try:
            alt = HOME / "OneDrive" / p.relative_to(HOME)
            if alt.exists() or alt.parent.exists():
                return alt
        except ValueError:
            pass
    return p

APP_ALIASES = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "file explorer": "explorer", "explorer": "explorer", "files": "explorer",
    "command prompt": "cmd", "cmd": "cmd", "powershell": "powershell", "terminal": "wt",
    "task manager": "taskmgr", "settings": "ms-settings:", "control panel": "control",
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "spotify": "spotify:", "camera": "microsoft.windows.camera:", "store": "ms-windows-store:",
    "snipping tool": "snippingtool", "clock": "ms-clock:", "mail": "outlookmail:",
}

VK = {"volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
      "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "play_pause": 0xB3}


def _press_vk(code, times=1):
    for _ in range(times):
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        time.sleep(0.02)


NICKNAMES = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
             "calc": "calculator", "explorer": "file explorer", "files": "file explorer", "cmd": "command prompt",
             "edge": "microsoft edge", "whats app": "whatsapp", "you tube": "youtube"}
GENERIC_WORDS = {"app", "application", "the", "my", "browser", "software", "program", "desktop", "pc", "laptop"}
_apps = []  # [(lowercase name, display name, AppID)] from Get-StartApps
_apps_ready = threading.Event()


def _load_installed_apps():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out or "[]")
        data = [data] if isinstance(data, dict) else data
        _apps[:] = [(a["Name"].lower(), a["Name"], a["AppID"]) for a in data if a.get("Name")]
    except Exception as e:
        print(f"[warn] couldn't list installed apps: {e}")
    _apps_ready.set()


threading.Thread(target=_load_installed_apps, daemon=True).start()


def find_app(name):
    """Best installed-app match for a spoken name: (display name, AppID) or None. Tolerates mishearings."""
    _apps_ready.wait(timeout=15)
    if not _apps:
        return None
    q = name.lower().strip()
    q = NICKNAMES.get(q, q)
    core = " ".join(w for w in q.split() if w not in GENERIC_WORDS) or q
    by_name = {n: (display, app_id) for n, display, app_id in _apps}
    for term in (q, core):
        if term in by_name:
            return by_name[term]
    starts = sorted((n for n in by_name if n.startswith(core + " ") or n.startswith(core)), key=len)
    if starts and len(core) >= 3:
        return by_name[starts[0]]
    inside = sorted((n for n in by_name if len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", q)), key=len, reverse=True)
    if inside:
        return by_name[inside[0]]
    close = difflib.get_close_matches(core, list(by_name), n=1, cutoff=0.7)
    if not close:  # compare against first words too, e.g. "cloud" -> "claude"
        firsts = {n.split()[0]: n for n in by_name if len(n.split()[0]) >= 4}
        hit = difflib.get_close_matches(core, list(firsts), n=1, cutoff=0.7)
        close = [firsts[hit[0]]] if hit else []
    return by_name[close[0]] if close else None


def installed_app(name):
    """Strict check: is an app with this name installed? (display name, AppID) or None. No fuzzy guessing."""
    _apps_ready.wait(timeout=15)
    name = name.lower().strip()
    hits = sorted(((n, d, a) for n, d, a in _apps if n == name or n.startswith(name + " ")
                   or n.endswith(" " + name)), key=lambda x: len(x[0]))
    return (hits[0][1], hits[0][2]) if hits else None


def open_application(name: str) -> str:
    """Open an installed application by (approximate) name. Returns text starting with 'Opened' or 'NOT_FOUND'."""
    app = find_app(name)
    if app:
        display, app_id = app
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
        return f"Opened {display}."
    target = APP_ALIASES.get(name.lower().strip())
    if target:
        try:
            os.startfile(target)
            return f"Opened {name}."
        except OSError:
            pass
    return f"NOT_FOUND: no installed app matching '{name}'."


def close_application(name: str) -> str:
    """Close a running application gracefully (it may still prompt to save work).

    Args:
        name: Application or process name, e.g. "notepad", "chrome", "spotify".
    """
    key = name.lower().replace(".exe", "").strip()
    keys = {APP_ALIASES.get(key, key).rstrip(":")}
    app = find_app(key)
    if app:  # e.g. "brave browser" -> "brave", "visual studio code" -> "code"
        words = app[0].lower().split()
        keys |= {words[0], words[-1]}
    vendors = {"microsoft", "google", "windows", "adobe", "visual", "apple", "mozilla"}
    keys = {k for k in keys if len(k) >= 3 and k not in vendors}
    names = {p.info["name"] for p in psutil.process_iter(["name"])
             if p.info["name"] and any(k in p.info["name"].lower() for k in keys)}
    if not names:
        return f"No running process matching '{name}'."
    for proc_name in names:
        subprocess.run(["taskkill", "/IM", proc_name], capture_output=True)
    return f"Asked {', '.join(sorted(names))} to close."


def list_running_apps() -> str:
    """List the titles of open windows on the PC."""
    titles = sorted({w.title for w in pyautogui.getAllWindows() if w.title.strip()})
    return "\n".join(titles[:60]) or "No windows found."


def open_website(url: str) -> str:
    """Open a website in the default browser.

    Args:
        url: Full URL or domain, e.g. "github.com".
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}."


def google_search(query: str) -> str:
    """Open a Google search for the query in the browser (use when the user wants to see results themselves).

    Args:
        query: What to search for.
    """
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Searching Google for {query}."


def play_on_youtube(query: str) -> str:
    """Open YouTube search results for a song or video.

    Args:
        query: Song, video, or channel name.
    """
    webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
    return f"Opened YouTube results for {query}."


def volume(action: str, amount: int = 10) -> str:
    """Change system volume.

    Args:
        action: One of "up", "down", "mute" (toggles mute), or "set".
        amount: Percent to change by for up/down, or the target percent (0-100) for "set".
    """
    steps = max(1, round(amount / 2))  # each key press is ~2%
    if action == "up":
        _press_vk(VK["volume_up"], steps)
    elif action == "down":
        _press_vk(VK["volume_down"], steps)
    elif action == "mute":
        _press_vk(VK["volume_mute"])
    elif action == "set":
        _press_vk(VK["volume_down"], 50)
        _press_vk(VK["volume_up"], max(0, min(100, amount)) // 2)
    else:
        return "Unknown volume action."
    return f"Volume {action} done."


def media_control(action: str) -> str:
    """Control music/video playback in any media app.

    Args:
        action: One of "play_pause", "next", "previous", "stop".
    """
    if action not in ("play_pause", "next", "previous", "stop"):
        return "Unknown media action."
    _press_vk(VK[action])
    return f"Media {action}."


def set_brightness(level: int) -> str:
    """Set screen brightness (works on laptop built-in displays).

    Args:
        level: Brightness percent, 0-100.
    """
    level = max(0, min(100, level))
    cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    return f"Brightness set to {level}%." if r.returncode == 0 else f"Could not set brightness: {r.stderr.strip()[:200]}"


def system_status() -> str:
    """Get CPU, memory, disk and battery status of the PC."""
    parts = [f"CPU {psutil.cpu_percent(interval=0.5)}%",
             f"RAM {psutil.virtual_memory().percent}% used",
             f"C: drive {psutil.disk_usage('C:/').percent}% full"]
    batt = psutil.sensors_battery()
    if batt:
        parts.append(f"battery {batt.percent:.0f}% ({'charging' if batt.power_plugged else 'on battery'})")
    return ", ".join(parts)


def get_datetime() -> str:
    """Get the current local date and time."""
    return datetime.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


def take_screenshot() -> str:
    """Take a screenshot and save it to Pictures/Jarvis."""
    folder = _resolve("~/Pictures") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    pyautogui.screenshot().save(path)
    return f"Saved screenshot to {path}."


def type_text(text: str) -> str:
    """Type text at the current cursor position (into whatever window is focused).

    Args:
        text: Text to type.
    """
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")  # paste is faster and handles unicode
    return "Typed it."


def press_keys(keys: str) -> str:
    """Press a keyboard shortcut or key, e.g. "ctrl+s", "alt+tab", "win+d", "enter".

    Args:
        keys: Keys joined with "+".
    """
    parts = [k.strip().lower().replace("windows", "win") for k in keys.split("+")]
    pyautogui.hotkey(*parts)
    return f"Pressed {keys}."


def clipboard(action: str, text: str = "") -> str:
    """Read or set the clipboard.

    Args:
        action: "get" or "set".
        text: Text to put on the clipboard when action is "set".
    """
    if action == "set":
        pyperclip.copy(text)
        return "Copied to clipboard."
    return pyperclip.paste()[:4000] or "Clipboard is empty."


def set_reminder(minutes: float, message: str) -> str:
    """Set a spoken reminder/timer that fires after some minutes while Jarvis is running.

    Args:
        minutes: Delay in minutes.
        message: What to say when it fires.
    """
    t = threading.Timer(minutes * 60, lambda: say(f"Reminder: {message}"))
    t.daemon = True
    t.start()
    return f"Reminder set for {minutes:g} minutes."


def lock_screen() -> str:
    """Lock the PC."""
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


def power(action: str) -> str:
    """Shut down, restart, sleep, or sign out of the PC. The user is asked to confirm first.

    Args:
        action: One of "shutdown", "restart", "sleep", "logoff".
    """
    commands = {"shutdown": "shutdown /s /t 5", "restart": "shutdown /r /t 5", "logoff": "shutdown /l",
                "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"}
    if action not in commands:
        return "Unknown power action."
    if not confirm(f"Are you sure you want me to {action} the computer?"):
        return "User cancelled."
    subprocess.Popen(commands[action], shell=True)
    return f"{action} started."


def run_powershell(command: str) -> str:
    """Run a PowerShell command and return its output. Use for anything the other tools can't do
    (e.g. Wi-Fi info, IP address, installing with winget, file operations). The user must confirm first.

    Args:
        command: PowerShell command to run.
    """
    if not confirm(f"I'd like to run this PowerShell command: {command}. Shall I go ahead?"):
        return "User declined to run the command."
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "Command timed out after 2 minutes."
    out = (r.stdout + ("\nERRORS:\n" + r.stderr if r.stderr.strip() else "")).strip()
    return (out or f"Done (exit code {r.returncode}).")[:6000]


def list_files(folder: str = "~") -> str:
    """List files in a folder. Shortcuts like "~", "~/Desktop", "~/Downloads", "~/Documents" work.

    Args:
        folder: Folder path.
    """
    p = _resolve(folder)
    if not p.is_dir():
        return f"{p} is not a folder."
    entries = sorted(p.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True)[:80]
    return "\n".join(("[dir] " if e.is_dir() else "") + e.name for e in entries) or "Folder is empty."


def open_file(path: str) -> str:
    """Open a file or folder with its default program.

    Args:
        path: File or folder path ("~" allowed).
    """
    p = _resolve(path)
    if not p.exists():
        return f"{p} does not exist."
    os.startfile(p)
    return f"Opened {p}."


def read_text_file(path: str) -> str:
    """Read a text file's contents.

    Args:
        path: File path ("~" allowed).
    """
    p = _resolve(path)
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:10000]
    except OSError as e:
        return f"Could not read {p}: {e}"


def write_text_file(path: str, content: str) -> str:
    """Create or overwrite a text file (e.g. a note on the Desktop). Asks before overwriting.

    Args:
        path: File path ("~" allowed), e.g. "~/Desktop/notes.txt".
        content: Text to write.
    """
    p = _resolve(path)
    if p.exists() and not confirm(f"{p.name} already exists. Overwrite it?"):
        return "User chose not to overwrite."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Saved {p}."


def take_note(text: str) -> str:
    """Append a timestamped line to jarvis_notes.txt on the Desktop."""
    p = _resolve("~/Desktop") / "jarvis_notes.txt"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%d %b %Y %I:%M %p}] {text}\n")
    return f"Noted in {p}."


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "JarvisAssistant/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def weather(city: str = "") -> str:
    """Current weather from wttr.in (free, no key). Empty city = your location by IP."""
    try:
        data = _get_json(f"https://wttr.in/{urllib.parse.quote(city)}?format=j1")
        now = data["current_condition"][0]
        area = city.title() if city else data["nearest_area"][0]["areaName"][0]["value"]
        return (f"In {area} it's {now['temp_C']} degrees and {now['weatherDesc'][0]['value'].lower()}, "
                f"feels like {now['FeelsLikeC']}, humidity {now['humidity']} percent.")
    except Exception as e:
        return f"I couldn't get the weather right now ({e})."


def wikipedia(topic: str) -> str:
    """Short spoken summary of a topic from Wikipedia (free, no key). Returns '' if nothing found."""
    try:
        q = urllib.parse.quote(topic)
        hits = _get_json(f"https://en.wikipedia.org/w/api.php?action=opensearch&limit=1&format=json&search={q}")
        if not hits[1]:
            return ""
        title = urllib.parse.quote(hits[1][0].replace(" ", "_"))
        extract = _get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}").get("extract", "")
        sentences = extract.split(". ")
        return ". ".join(sentences[:2]).rstrip(".") + "." if extract else ""
    except Exception:
        return ""
