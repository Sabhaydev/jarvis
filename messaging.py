"""Write emails and chat messages in real apps. NOTHING is sent without the user's explicit yes.

Flow for every message: open the app (installed app if present, otherwise its web version) ->
type the draft into it -> show/speak a preview and ask -> only on "yes" press the send key.
On "no" the draft is cleared (chats) or left open for editing (email).
"""

import json
import os
import re
import time
import urllib.parse
import webbrowser
from pathlib import Path

import pyautogui
import pygetwindow as gw
import pyperclip

import pc_tools as pc

CONTACTS_FILE = Path(__file__).with_name("contacts.json")


# ---------------- contacts ----------------
def load_contacts():
    try:
        return {k.lower(): v for k, v in json.loads(CONTACTS_FILE.read_text(encoding="utf-8")).items()}
    except (OSError, json.JSONDecodeError):
        return {}


def lookup(name, field):
    entry = load_contacts().get(name.lower().strip())
    return entry.get(field) if isinstance(entry, dict) else None


def remember(name, field, value):
    contacts = load_contacts()
    contacts.setdefault(name.lower().strip(), {})[field] = value
    CONTACTS_FILE.write_text(json.dumps(contacts, indent=2), encoding="utf-8")


def spoken_email(text):
    """'rahul dot sharma at the rate gmail dot com' -> 'rahul.sharma@gmail.com' (None if not an address)."""
    t = text.lower().strip()
    t = re.sub(r"\s*\b(at the rate|at the rate of|at)\b\s*", "@", t) if "@" not in t else t
    t = re.sub(r"\s*\b(dot|point)\b\s*", ".", t)
    t = re.sub(r"\s*\bunderscore\b\s*", "_", t)
    t = re.sub(r"\s*\b(dash|hyphen)\b\s*", "-", t)
    t = t.replace(" ", "")
    return t if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t) else None


# ---------------- window helpers ----------------
def wait_window(title_part, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if any(title_part.lower() in w.title.lower() for w in gw.getAllWindows()):
            return True
        time.sleep(0.5)
    return False


class NotInFront(Exception):
    pass


def focus(title_part):
    """Bring a window to the front. Returns True only if it really is the active window afterwards."""
    for _ in range(3):
        wins = [w for w in gw.getAllWindows() if w.title and title_part.lower() in w.title.lower()]
        if not wins:
            return False
        w = wins[0]
        try:
            if w.isMinimized:
                w.restore()
            pyautogui.press("alt")  # lets Windows hand focus to another window
            w.activate()
        except Exception:
            pass
        time.sleep(0.5)
        active = gw.getActiveWindow()
        if active and title_part.lower() in active.title.lower():
            return True
    return False


def must_focus(title_part):
    """Like focus(), but stops everything (no keys are sent) if the window can't be brought to the front."""
    if not focus(title_part):
        raise NotInFront(title_part)


def guarded(fn):
    """Never type into the wrong window: abort the whole action if the target app isn't in front."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotInFront as e:
            return (f"STOPPED: I couldn't bring {e} to the front, so I stopped without typing or sending anything. "
                    f"Please leave the keyboard and mouse for a moment and try again.")
    return wrapper


# ---------------- WhatsApp desktop app via UI Automation ----------------
def _uia():
    import uiautomation as auto
    auto.Logger.SetLogFile(os.path.join(os.environ.get("TEMP", "."), "jarvis_uia.log"))
    return auto


def _find(win, pred, depth=70):
    auto = _uia()
    for c, _ in auto.WalkControl(win, maxDepth=depth):
        try:
            if pred(c):
                return c
        except Exception:
            continue
    return None


def wa_window():
    """Open/raise the WhatsApp app and return its UI Automation control (it must end up in front)."""
    if not any(w.title.strip() == "WhatsApp" for w in gw.getAllWindows()):
        pc.open_application("whatsapp")
        if not wait_window("WhatsApp", 25):
            raise NotInFront("WhatsApp")
        time.sleep(3)
    must_focus("WhatsApp")
    hwnd = gw.getActiveWindow()._hWnd
    return _uia().ControlFromHandle(hwnd)


def wa_open_chat(name):
    """Search for a chat by name and open the best match. Returns (window control, chat label) or raises."""
    win = wa_window()
    box = _find(win, lambda c: c.ControlTypeName == "EditControl" and "search" in (c.Name or "").lower())
    if box is None:
        raise LookupError("I couldn't find WhatsApp's search box.")
    must_focus("WhatsApp")
    box.Click(simulateMove=False)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    paste(name)
    time.sleep(2)
    want = name.lower().strip()
    item = (_find(win, lambda c: c.ControlTypeName == "DataItemControl" and c.IsKeyboardFocusable
                  and (c.Name or "").lower().startswith(want))
            or _find(win, lambda c: c.ControlTypeName == "DataItemControl" and c.IsKeyboardFocusable
                     and want.split()[0] in (c.Name or "").lower()))
    if item is None:
        raise LookupError(f"I couldn't find a WhatsApp chat called {name}.")
    label = re.split(r"\s\d{1,2}:\d{2}|\s(?:yesterday|today)\b", item.Name, maxsplit=1, flags=re.I)[0].strip()
    must_focus("WhatsApp")
    item.Click(simulateMove=False)
    for _ in range(8):  # wait until the chat header (with its call buttons) is showing
        time.sleep(0.5)
        if _header_call_button(win, False) is not None:
            return win, label
    raise LookupError(f"I found {label} but the chat didn't open. Please keep WhatsApp in front and try again.")


def wa_type(win, text):
    """Type into the message box of the open chat (without sending)."""
    box = _find(win, lambda c: c.ControlTypeName == "EditControl" and "search" not in (c.Name or "").lower()
                and any(k in (c.Name or "").lower() for k in ("message", "type")))
    must_focus("WhatsApp")
    if box is not None:
        box.Click(simulateMove=False)
        time.sleep(0.3)
    paste(text)


def _header_call_button(win, video):
    """The chat header's exact 'Voice call' / 'Video call' button (not old call bubbles in the chat)."""
    auto = _uia()
    want = "video call" if video else "voice call"
    hits = [c for c, _ in auto.WalkControl(win, maxDepth=200)
            if c.ControlTypeName == "ButtonControl" and (c.Name or "").strip().lower() == want]
    return min(hits, key=lambda c: c.BoundingRectangle.top) if hits else None


def wa_click_call(win, video):
    """Press the voice/video call button in the open chat's header."""
    btn = _header_call_button(win, video)
    if btn is None:
        raise LookupError("I couldn't find WhatsApp's call button. Make sure the chat is open and try again.")
    auto = _uia()
    invoke = btn.GetPattern(auto.PatternId.InvokePattern)
    if invoke is not None:
        invoke.Invoke()
    else:
        must_focus("WhatsApp")
        btn.Click(simulateMove=False)


@guarded
def whatsapp_message(to: str, text: str) -> str:
    """Draft a WhatsApp message in the desktop app (or WhatsApp Web), then send only if the user says yes."""
    number = lookup(to, "phone")
    if not number and re.fullmatch(r"\+?[\d\s-]{8,}", to):
        number = re.sub(r"[^\d]", "", to)
    number = re.sub(r"[^\d]", "", number) if number else None
    app = pc.installed_app("whatsapp")

    if app:
        label = "WhatsApp app"
        if number:
            os.startfile(f"whatsapp://send?phone={number}&text={urllib.parse.quote(text)}")
            wait_window("WhatsApp")
            time.sleep(3)
        else:
            try:
                win, chat = wa_open_chat(to)
            except LookupError as e:
                return str(e)
            to = f"{chat}" if chat else to
            wa_type(win, text)
    else:
        label = "WhatsApp Web"
        if number:
            webbrowser.open(f"https://web.whatsapp.com/send?phone={number}&text={urllib.parse.quote(text)}")
            wait_window("WhatsApp", 30)
            time.sleep(10)
        else:
            webbrowser.open("https://web.whatsapp.com")
            if not wait_window("WhatsApp", 30):
                return "WhatsApp Web didn't load."
            time.sleep(10)
            must_focus("WhatsApp")
            pyautogui.hotkey("ctrl", "alt", "/")  # WhatsApp Web: search chats
            time.sleep(0.8)
            paste(to)
            time.sleep(2.5)
            pyautogui.press("enter")
            time.sleep(1.5)
            paste(text)

    def send():
        must_focus("WhatsApp")
        pyautogui.press("enter")

    def cancel():
        must_focus("WhatsApp")
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        return "CANCELLED: nothing was sent and the draft was cleared."

    return _confirm_and_send(label, to, text, send, cancel)


# ---------------- Telegram ----------------
@guarded
def telegram_message(to: str, text: str) -> str:
    """Draft a Telegram message (desktop app or Telegram Web), then send only if the user says yes."""
    if pc.installed_app("telegram"):
        label = "Telegram app"
        pc.open_application("telegram")
        if not wait_window("Telegram"):
            return "Telegram didn't open."
        time.sleep(2.5)
        must_focus("Telegram")
        pyautogui.press("esc")
        pyautogui.press("esc")  # back to the chat list; typing now searches
        time.sleep(0.5)
    else:
        label = "Telegram Web"
        webbrowser.open("https://web.telegram.org/a/")
        if not wait_window("Telegram", 30):
            return "Telegram Web didn't load."
        time.sleep(8)
        must_focus("Telegram")
        pyautogui.press("esc")
    paste(to)
    time.sleep(2)
    pyautogui.press("enter")
    time.sleep(1.5)
    paste(text)

    def send():
        must_focus("Telegram")
        pyautogui.press("enter")

    def cancel():
        must_focus("Telegram")
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        return "CANCELLED: nothing was sent and the draft was cleared."

    return _confirm_and_send(label, to, text, send, cancel)


# ---------------- Email ----------------
def resolve_email(to):
    return lookup(to, "email") or spoken_email(to) or (to if "@" in to else None)


@guarded
def email_message(to: str, subject: str, body: str, provider: str = "") -> str:
    """Draft an email in Outlook (app if installed) or Gmail (web), then send only if the user says yes."""
    address = resolve_email(to)
    if not address:
        return f"NO_ADDRESS: I don't have an email address for {to}."
    provider = (provider or os.environ.get("JARVIS_EMAIL_APP", "gmail")).lower()
    q = urllib.parse.quote

    if provider == "outlook" and pc.installed_app("outlook"):
        label, title = "Outlook app", subject or "Message"
        os.startfile(f"mailto:{address}?subject={q(subject)}&body={q(body)}")
        wait_window(title, 20)
        time.sleep(2)
    elif provider == "outlook":
        label, title = "Outlook on the web", "Outlook"
        webbrowser.open(f"https://outlook.live.com/mail/0/deeplink/compose?to={q(address)}"
                        f"&subject={q(subject)}&body={q(body)}")
        wait_window("Outlook", 30)
        time.sleep(8)
    else:
        label, title = "Gmail", "Gmail"  # Gmail has no Windows app, so it always opens on the web
        webbrowser.open(f"https://mail.google.com/mail/?view=cm&fs=1&to={q(address)}&su={q(subject)}&body={q(body)}")
        wait_window("Gmail", 30)
        time.sleep(6)

    def send():
        must_focus(title)
        pyautogui.hotkey("ctrl", "enter")  # send shortcut in both Gmail and Outlook

    def cancel():
        return "CANCELLED: nothing was sent. The draft is still open if you want to edit it."

    return _confirm_and_send(label, address, body, send, cancel, subject=subject)


# ---------------- Calls (always confirmed first) ----------------
def _number_for(to):
    number = lookup(to, "phone")
    if not number and re.fullmatch(r"\+?[\d\s-]{6,}", to):
        number = to
    return re.sub(r"[^\d+]", "", number) if number else None


def _confirm_call(kind, to, via, extra=""):
    details = {"call": kind, "to": to, "using": via}
    return pc.confirm(f"Shall I start a {kind} with {to} using {via}?{extra}", details)


@guarded
def whatsapp_call(to: str, video: bool = False) -> str:
    """Open the WhatsApp chat with someone and start a voice/video call - only after the user says yes."""
    if not pc.installed_app("whatsapp"):
        return "NO_APP: WhatsApp calls need the WhatsApp desktop app, which isn't installed."
    kind = "video call" if video else "voice call"
    number = _number_for(to)
    try:
        if number:
            os.startfile(f"whatsapp://send?phone={number.lstrip('+')}")
            wait_window("WhatsApp")
            time.sleep(3)
            win = wa_window()
            chat = to
        else:
            win, chat = wa_open_chat(to)
    except LookupError as e:
        return str(e)
    if not _confirm_call(kind, chat, "WhatsApp", " Check that the right chat is open."):
        return "CANCELLED: no call was made."
    try:
        wa_click_call(win, video)
    except LookupError as e:
        return str(e)
    return f"CALLING: WhatsApp {kind} to {chat}."


def phone_call(to: str) -> str:
    """Call a phone number through Phone Link (your linked mobile) - only after the user says yes."""
    number = _number_for(to)
    if not number:
        return f"NO_NUMBER: I don't have a phone number for {to}."
    if not pc.installed_app("phone link"):
        return "NO_APP: Phone Link isn't installed, so I can't make phone calls from this PC."
    if not _confirm_call("phone call", f"{to} ({number})" if number != to else number, "Phone Link"):
        return "CANCELLED: no call was made."
    os.startfile(f"tel:{number}")
    return f"CALLING: phone call to {number} via Phone Link."


def teams_call(to: str, video: bool = False) -> str:
    """Start a Microsoft Teams call to someone's email - only after the user says yes."""
    from urllib.parse import quote
    address = lookup(to, "teams") or lookup(to, "email") or spoken_email(to) or (to if "@" in to else None)
    if not address:
        return f"NO_ADDRESS: I don't have a Teams or email address for {to}."
    kind = "video call" if video else "voice call"
    via = "Teams app" if pc.installed_app("microsoft teams") else "Teams on the web"
    if not _confirm_call(kind, f"{to} ({address})" if address != to else address, via):
        return "CANCELLED: no call was made."
    url = f"https://teams.microsoft.com/l/call/0/0?users={quote(address)}" + ("&withVideo=true" if video else "")
    os.startfile(url.replace("https://", "msteams://", 1)) if via == "Teams app" else webbrowser.open(url)
    return f"CALLING: Teams {kind} to {to}."


def call_control(action: str) -> str:
    """Control an active WhatsApp call: 'end', 'mute' (toggle) or 'camera' (toggle)."""
    if not focus("WhatsApp"):
        return "I can't bring the WhatsApp call window to the front."
    keys = {"end": ("esc",), "mute": ("ctrl", "shift", "m"), "camera": ("ctrl", "shift", "o")}[action]
    pyautogui.hotkey(*keys)
    return {"end": "Call ended.", "mute": "Toggled mute.", "camera": "Toggled the camera."}[action]
