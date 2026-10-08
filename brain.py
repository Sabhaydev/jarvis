"""Jarvis's brain: a free, offline command engine that maps what you say to PC actions.

Each rule is a regex plus a handler. To teach Jarvis something new, add a method and a rule in RULES.
"""

import os
import random
import re

import pc_tools as pc
from events import bus

SITES = {
    "youtube": "youtube.com", "google": "google.com", "gmail": "mail.google.com", "github": "github.com",
    "instagram": "instagram.com", "facebook": "facebook.com", "twitter": "x.com", "x": "x.com",
    "whatsapp": "web.whatsapp.com", "whatsapp web": "web.whatsapp.com", "linkedin": "linkedin.com",
    "netflix": "netflix.com", "amazon": "amazon.in", "flipkart": "flipkart.com", "chatgpt": "chatgpt.com",
    "reddit": "reddit.com", "stack overflow": "stackoverflow.com", "wikipedia": "wikipedia.org",
    "google drive": "drive.google.com", "maps": "maps.google.com", "google maps": "maps.google.com",
    "hotstar": "hotstar.com", "spotify": "open.spotify.com", "telegram": "web.telegram.org", "prime video": "primevideo.com", "claude": "claude.ai", "brave": "brave.com",
}
# Things that exist both as an installed app and a website: app if installed, otherwise web.
APP_OR_WEB = {
    "whatsapp": "WhatsApp", "telegram": "Telegram", "spotify": "Spotify", "netflix": "Netflix",
    "instagram": "Instagram", "facebook": "Facebook", "linkedin": "LinkedIn", "chatgpt": "ChatGPT",
    "claude": "Claude", "prime video": "Prime Video", "hotstar": "Hotstar", "outlook": "Outlook",
    "teams": "Microsoft Teams", "microsoft teams": "Microsoft Teams", "discord": "Discord", "slack": "Slack",
    "zoom": "Zoom", "notion": "Notion", "figma": "Figma", "skype": "Skype", "x": "X", "twitter": "X",
    "messenger": "Messenger", "snapchat": "Snapchat", "pinterest": "Pinterest", "canva": "Canva",
}
SITES.update({"outlook": "outlook.live.com", "teams": "teams.microsoft.com", "microsoft teams": "teams.microsoft.com",
              "discord": "discord.com/app", "slack": "app.slack.com", "zoom": "app.zoom.us", "notion": "notion.so",
              "figma": "figma.com", "skype": "web.skype.com", "messenger": "messenger.com", "snapchat": "web.snapchat.com",
              "pinterest": "pinterest.com", "canva": "canva.com"})
MESSAGE_APPS = r"whatsapp|whats app|telegram|gmail|outlook|e-?mail|mail|text|sms|message|msg"
FOLDERS = {"desktop": "~/Desktop", "downloads": "~/Downloads", "documents": "~/Documents",
           "pictures": "~/Pictures", "music": "~/Music", "videos": "~/Videos", "home": "~"}
JOKES = [
    "I would tell you a UDP joke, but you might not get it.",
    "There are ten kinds of people: those who understand binary and those who don't.",
    "I told my computer I needed a break. It said: no problem, I'll go to sleep.",
    "Why do programmers prefer dark mode? Because light attracts bugs.",
]
NUM = r"(\d{1,3})"


HI_DO = r" ?(?:kar|karo|kardo|kar do|kar de|karde|de|do|dena|dijiye|kijiye)?"
HINGLISH = [
    (r"\b(yaar|yar|bhai|zara|jaldi|abhi|ji)\b", ""),
    (r"\s+na$", ""),
    (r"^(.+?) ko (whatsapp|telegram|mail|email) (?:pe|par|per|par se|se)? ?(?:message|msg|mail)? ?(?:bhejo|bhej do|bhej de|karo|kar do|kar de|send karo|send kar do)(?: ki| ke| that)? ?(.*)$",
     r"send \2 message to \1 saying \3"),
    (r"^(?:call|phone) (?:kaato|kaat do|kaat de|band karo|band kar do|rakho|rakh do|cut karo)$", "end the call"),
    (r"^(.+?) ko (?:(whatsapp|phone|teams|mobile) (?:pe |par |per |se )?)?(video )?call (?:karo|kar|kar do|kar de|lagao|laga|laga do|laga de|milao|mila do|mila de)$",
     r"\3call \1 on \2"),
    (r" on$", ""),
    (r"^(?:mera |my )?(computer|pc|laptop|system) (?:band|shut ?down|off)" + HI_DO + "$", r"shut down the \1"),
    (r"^(.+?) (?:ko )?(?:open|khol|kholo|kholdo|start|chalu|launch)" + HI_DO + "$", r"open \1"),
    (r"^(.+?) (?:ko )?(?:band|close)" + HI_DO + "$", r"close \1"),
    (r"^(.+?) (?:ko )?(?:chalao|chala|bajao|baja|lagao|laga|play)" + HI_DO + "$", r"play \1"),
    (r"^(?:(?:awaaz|awaz|aawaz|volume|sound) )(?:badhao|badha|tez|zyada|upar)" + HI_DO + "$", "volume up"),
    (r"^(?:(?:awaaz|awaz|aawaz|volume|sound) )(?:kam|dheere|dheema|ghatao|neeche)" + HI_DO + "$", "volume down"),
    (r"kitne baje|time kya (?:hai|hua)|samay kya", "what is the time"),
    (r"^(.+?) (?:ka|me|mein) mausam.*", r"weather in \1"),
    (r"^mausam.*", "weather"),
    (r"screenshot (?:lo|le lo|lelo|le)", "take a screenshot"),
]
HINGLISH = [(re.compile(p), r) for p, r in HINGLISH]
COMMAND_START = re.compile(r"\b(open|launch|play|close|send|write an?|compose|message|whatsapp|email|mail|search for|"
                           r"search|remind me|set a timer|video call|call|dial|take a screenshot|lock the|volume|brightness|weather|what is the time)\b")


def hinglish(text):
    """Rewrite common Hindi/Hinglish phrasing into the English commands below."""
    for pattern, repl in HINGLISH:
        text = pattern.sub(repl, text).strip()
    return re.sub(r"\s+", " ", text)


def messaging_fn(name):
    import messaging
    return getattr(messaging, name)


def parse_message(text):
    """Pull (app, recipient, subject, body) out of a request like
    'send a whatsapp message to rahul saying I'll be late' or 'email boss about leave saying ...'."""
    t = " " + text + " "
    app = None
    m = re.search(r" (?:on|via|through|using|in|from|by) (whatsapp|whats app|telegram|gmail|outlook|email|mail) ", t)
    if m:
        app = m.group(1).replace("whats app", "whatsapp")
        t = t[:m.start()] + " " + t[m.end():]
    else:
        m = re.search(r"\b(whatsapp|whats app|telegram|gmail|outlook|e-?mail|mail)\b", t)
        if m:
            app = m.group(1).replace("whats app", "whatsapp").replace("e-mail", "email")
    if app == "mail":
        app = "email"
    body = None
    m = re.search(r"\b(?:saying|that says|which says|and say|and tell (?:him|her|them)|telling (?:him|her|them)|"
                  r"tell (?:him|her|them)|with the message|with message|the message is|message is|body is|and write|"
                  r"and the message is)\b(?: that)? (.+)$", t)
    if not m:
        m = re.search(r"\bthat (.+)$", t)
    if m:
        body = m.group(1).strip()
        t = t[:m.start()]
    subject = None
    m = re.search(r"\b(?:with (?:the )?subject|subject|about|regarding)\b (.+)$", t)
    if m:
        subject = m.group(1).strip()
        t = t[:m.start()]
    to = re.sub(r"^\s*(?:please )?(?:send|write|compose|draft|drop|shoot)?\s*(?:an?|the|one)?\s*"
                r"(?:new )?(?:whatsapp|whats app|telegram|gmail|outlook|text|sms|e-?mail|mail)?\s*"
                r"(?:message|msg|mail|email|note)?\s*(?:to|for)?\s*", "", t).strip()
    to = re.sub(r"\s+(on|via|to)$", "", to).strip()
    to = re.sub(r"^my ", "", to)
    words = to.split()
    if body is None and len(words) >= 3 and app not in ("email", "gmail", "outlook") and "@" not in to:
        to, body = words[0], " ".join(words[1:])  # "whatsapp papa I reached home"
    return app, to or None, subject, body


def parse_call(text):
    """'video call mom on whatsapp' -> ('whatsapp', 'mom', True)"""
    video = bool(re.search(r"\bvideo\b", text))
    app = None
    if re.search(r"\bwhatsapp|whats app\b", text):
        app = "whatsapp"
    elif re.search(r"\bteams\b", text):
        app = "teams"
    elif re.search(r"\b(phone|mobile|dial|normal|sim|cellular)\b", text):
        app = "phone"
    t = re.sub(r"\b(?:on|via|using|through|from|with) (?:my )?(?:whatsapp|whats app|teams|microsoft teams|phone link|phone|mobile)\b", "", text)
    m = re.search(r"\b(?:call|dial|ring)\b(?: to)? (.+)$", t) or re.search(r"\bphone\b(?: to)? (.+)$", t)
    to = m.group(1).strip() if m else ""
    to = re.sub(r"^(?:my )", "", to).strip()
    to = re.sub(r"\s+(?:now|please|right now)$", "", to)
    if re.fullmatch(r"\+?[\d\s-]{6,}", to):
        app = app or "phone"
    if re.fullmatch(r"(karo|kar do|kar de|kar|lagao|laga do|milao|someone|somebody|now|kisi ko)", to or ""):
        to = ""
    return app, to or None, video


class Brain:
    name = "Built-in command engine"

    def __init__(self):
        title = os.environ.get("JARVIS_USER_TITLE", "").strip()
        self.title = f", {title}" if title else ""
        self.rules = [(re.compile(p), getattr(self, h)) for p, h in RULES]

    # ---------- plumbing ----------
    def use(self, fn, **kwargs):
        """Run a PC tool and show it on the dashboard."""
        call = bus.emit("tool", name=fn.__name__, args=kwargs, status="running")
        try:
            result = fn(**kwargs)
            bus.emit("tool", name=fn.__name__, args=kwargs, status="done", result=str(result)[:500], ref=call["id"])
            return result
        except Exception as e:
            bus.emit("tool", name=fn.__name__, args=kwargs, status="error", result=str(e), ref=call["id"])
            return f"That failed: {e}"

    def handle(self, text, _nested=False):
        clean = re.sub(r"[^\w\s:+.'/-]", "", text.lower()).strip()
        clean = re.sub(r"^(please |can you |could you |would you |hey |ok |okay )+", "", clean)
        clean = re.sub(r"\s+please$", "", clean)
        clean = re.sub(r"\b(what|who|where|how|that)'s\b", r"\1 is", clean)
        clean = hinglish(clean)
        for pattern, handler in self.rules:
            m = pattern.search(clean)
            if m:
                bus.emit("intent", text=clean, intent=handler.__name__, groups=[g for g in m.groups() if g])
                return handler(m)
        inner = COMMAND_START.search(clean)
        if inner and inner.start() > 0 and not _nested:  # "do me a favour and open gmail" -> "open gmail"
            return self.handle(clean[inner.start():], _nested=True)
        bus.emit("intent", text=clean, intent="fallback", groups=[])
        return self.fallback(clean)

    # ---------- conversation ----------
    def greet(self, m):
        return random.choice([f"Hello{self.title}. What can I do for you?", f"At your service{self.title}.",
                              f"Good to hear from you{self.title}. How can I help?"])

    def how_are_you(self, m):
        return f"All systems running smoothly{self.title}. Thanks for asking."

    def thanks(self, m):
        return random.choice([f"Anytime{self.title}.", f"My pleasure{self.title}.", "Happy to help."])

    def who_are_you(self, m):
        return "I'm JARVIS, your personal assistant. I can open apps and websites, control media, volume and brightness, take notes, set reminders, check the weather, and look things up."

    def help(self, m):
        return ("Try: open Chrome, play a song on YouTube, volume up, brightness 60, what's the weather, "
                "who is Elon Musk, take a screenshot, remind me in 5 minutes to stretch, note buy milk, or lock the PC.")

    def joke(self, m):
        return random.choice(JOKES)

    # ---------- info ----------
    def time(self, m):
        return "It's " + self.use(pc.get_datetime).split(", ")[-1] + "."

    def date(self, m):
        return "Today is " + ", ".join(self.use(pc.get_datetime).split(", ")[:2]) + "."

    def status(self, m):
        s = self.use(pc.system_status)
        return s[0].upper() + s[1:] + "."

    def battery(self, m):
        s = self.use(pc.system_status)
        part = next((p for p in s.split(", ") if p.startswith("battery")), None)
        return (part.capitalize() + ".") if part else "I can't find a battery on this PC."

    def weather(self, m):
        city = (m.group(1) or m.group(2) or "").strip()
        return self.use(pc.weather, city=city)

    def lookup(self, m):
        topic = m.group(2).strip()
        topic = topic[topic.startswith(("a ", "an ", "the ")) and topic.index(" ") + 1:]
        answer = self.use(pc.wikipedia, topic=topic)
        if answer:
            return answer
        self.use(pc.google_search, query=m.group(0))
        return f"I couldn't find a quick answer, so I've searched Google for {topic}."

    # ---------- apps & web ----------
    def open_thing(self, m):
        target = m.group(2).strip()
        target = re.sub(r"^(the |my )", "", target)
        wants_web = bool(re.search(r" (website|site|web)$", target))
        target = re.sub(r" (on|in) (my |the )?(desktop|pc|laptop|computer|system)$", "", target)
        target = re.sub(r" (app|application|website|site|web)$", "", target)
        if target in FOLDERS or target.endswith(" folder") and target[:-7] in FOLDERS:
            folder = FOLDERS.get(target) or FOLDERS[target[:-7]]
            self.use(pc.open_file, path=folder)
            return f"Opening {target}."
        if re.search(r"\.(com|in|org|net|io|ai|dev|co)\b", target):
            self.use(pc.open_website, url=target.replace(" ", ""))
            return f"Opening {target}."
        if target in APP_OR_WEB:
            app = None if wants_web else pc.installed_app(APP_OR_WEB[target])
            if app:
                self.use(pc.open_application, name=app[0])
                return f"Opening the {app[0]} app."
            self.use(pc.open_website, url=SITES[target])
            if wants_web:
                return f"Opening {target} on the web."
            return f"{APP_OR_WEB[target]} isn't installed, so I'm opening the web version."
        if target in SITES:
            self.use(pc.open_website, url=SITES[target])
            return f"Opening {target} in your browser."
        result = self.use(pc.open_application, name=target)  # also tries built-ins like task manager
        if result.startswith("Opened"):
            return "Opening " + result[len("Opened "):]
        return f"I couldn't find an app called {target} on this PC. Say search for {target} if you'd like me to look it up."

    def close_thing(self, m):
        target = re.sub(r"^(the |my )", "", m.group(2).strip())
        result = self.use(pc.close_application, name=target)
        return f"Closing {target}." if result.startswith("Asked") else result

    def windows(self, m):
        titles = self.use(pc.list_running_apps).splitlines()
        return f"You have {len(titles)} windows open, including " + ", ".join(t.split(" - ")[-1] for t in titles[:5]) + "."

    def youtube(self, m):
        query = next(g for g in m.groups() if g).strip()
        self.use(pc.play_on_youtube, query=query)
        return f"Here's {query} on YouTube."

    def search(self, m):
        query = m.group(2).strip()
        self.use(pc.google_search, query=query)
        return f"Searching Google for {query}."

    # ---------- media / volume / brightness ----------
    def volume_set(self, m):
        level = int(m.group(1))
        self.use(pc.volume, action="set", amount=level)
        return f"Volume set to {level} percent."

    def volume_up(self, m):
        amount = int(m.group(1)) if m.group(1) else 10
        self.use(pc.volume, action="up", amount=amount)
        return "Volume up."

    def volume_down(self, m):
        amount = int(m.group(1)) if m.group(1) else 10
        self.use(pc.volume, action="down", amount=amount)
        return "Volume down."

    def mute(self, m):
        self.use(pc.volume, action="mute")
        return "Done."

    def media(self, m):
        word = next(g for g in m.groups() if g)
        action = {"next": "next", "skip": "next", "previous": "previous", "last": "previous",
                  "stop": "stop"}.get(word, "play_pause")
        self.use(pc.media_control, action=action)
        return {"next": "Next track.", "previous": "Previous track.", "stop": "Stopped."}.get(action, "Done.")

    def brightness(self, m):
        level = int(m.group(1))
        return self.use(pc.set_brightness, level=level)

    def brightness_rel(self, m):
        level = 90 if (m.group(1) or m.group(2)) in ("increase", "raise", "up") else 30
        return self.use(pc.set_brightness, level=level)

    # ---------- keyboard / clipboard / screen ----------
    def screenshot(self, m):
        return self.use(pc.take_screenshot).replace("Saved screenshot to", "Screenshot saved in")

    def type_text(self, m):
        self.use(pc.type_text, text=m.group(1))
        return "Typed."

    def press(self, m):
        keys = m.group(1).replace("control", "ctrl").replace("windows", "win").replace("escape", "esc")
        keys = "+".join(k for k in re.split(r"[\s+]+", re.sub(r"\b(plus|and)\b", " ", keys)) if k)
        self.use(pc.press_keys, keys=keys)
        return f"Pressed {keys.replace('+', ' ')}."

    def clipboard_read(self, m):
        return "Your clipboard says: " + self.use(pc.clipboard, action="get")[:300]

    def minimize_all(self, m):
        self.use(pc.press_keys, keys="win+d")
        return "Showing the desktop."

    # ---------- productivity ----------
    def remind(self, m):
        amount, unit, message = float(m.group(1)), m.group(2), (m.group(3) or "time's up").strip()
        minutes = amount * 60 if unit.startswith("hour") else amount / 60 if unit.startswith("sec") else amount
        self.use(pc.set_reminder, minutes=minutes, message=message)
        return f"I'll remind you in {m.group(1)} {unit}."

    def note(self, m):
        self.use(pc.take_note, text=m.group(2).strip())
        return "Noted."

    def list_folder(self, m):
        name = m.group(1)
        files = self.use(pc.list_files, folder=FOLDERS[name]).splitlines()
        if not files or files[0].endswith("empty."):
            return f"Your {name} folder is empty."
        return f"Your {name} has {len(files)} items. The newest are " + ", ".join(files[:4]).replace("[dir] ", "") + "."

    # ---------- messaging (always asks before sending) ----------
    def message(self, m):
        app, to, subject, body = parse_message(m.string)
        is_email = app in ("email", "gmail", "outlook")
        app = app or os.environ.get("JARVIS_MESSAGE_APP", "whatsapp")
        if not to:
            to = pc.ask("Who should I send it to?")
        if not to:
            return "Okay, I've cancelled the message."
        if is_email:
            if subject is None:
                subject = pc.ask("What's the subject? Say skip for none.")
                subject = "" if re.fullmatch(r"(skip|none|no subject|nothing)\W*", subject.lower().strip()) else subject
            if not body:
                body = pc.ask("What should the email say?")
        elif not body:
            body = pc.ask("What should the message say?")
        if not body:
            return "Okay, I've cancelled the message."
        body = body[0].upper() + body[1:]
        subject = (subject[0].upper() + subject[1:]) if subject else subject

        if is_email:
            import messaging
            if not messaging.resolve_email(to):
                spelled = pc.ask(f"What's {to}'s email address? You can say it like rahul at gmail dot com.")
                address = messaging.spoken_email(spelled)
                if not address:
                    return "I couldn't understand that email address, so I've cancelled."
                messaging.remember(to, "email", address)
                bus.log(f"Saved {to}'s email to contacts.json")
            provider = "outlook" if app == "outlook" else ("gmail" if app == "gmail" else "")
            result = self.use(messaging_fn("email_message"), to=to, subject=subject, body=body, provider=provider)
        elif app == "telegram":
            result = self.use(messaging_fn("telegram_message"), to=to, text=body)
        else:
            result = self.use(messaging_fn("whatsapp_message"), to=to, text=body)

        if result.startswith("SENT"):
            return f"Sent to {to}."
        if result.startswith("CANCELLED"):
            return "Okay, I didn't send it." + (" The draft is still open." if "still open" in result else "")
        return result.split(": ", 1)[-1] if result.startswith(("STOPPED", "NO_")) else result

    def open_chat(self, m):
        name = m.group(1).strip()
        import messaging

        def open_whatsapp_chat(name: str) -> str:
            try:
                _, chat = messaging.wa_open_chat(name)
                return f"Opened {chat}"
            except (LookupError, messaging.NotInFront) as e:
                return f"FAILED: {e}"
        open_whatsapp_chat.__name__ = "open_whatsapp_chat"
        result = self.use(open_whatsapp_chat, name=name)
        return f"{result}'s chat is open on WhatsApp." if result.startswith("Opened") else result.split(": ", 1)[-1]

    def phone_link(self, m):
        result = self.use(pc.open_application, name="phone link")
        return "Opening Phone Link." if result.startswith("Opened") else "Phone Link isn't installed."

    # ---------- calls (always asks before calling) ----------
    def call(self, m):
        app, to, video = parse_call(m.string)
        if not to:
            to = pc.ask("Who should I call?")
        if not to:
            return "Okay, no call."
        app = app or os.environ.get("JARVIS_CALL_APP", "whatsapp")
        if app == "phone":
            import messaging
            if not messaging._number_for(to):
                spoken = pc.ask(f"What's {to}'s phone number?")
                digits = re.sub(r"[^\d+]", "", spoken)
                if len(digits) < 6:
                    return "I didn't catch a phone number, so I've cancelled the call."
                messaging.remember(to, "phone", digits)
                bus.log(f"Saved {to}'s number to contacts.json")
            result = self.use(messaging_fn("phone_call"), to=to)
        elif app == "teams":
            result = self.use(messaging_fn("teams_call"), to=to, video=video)
        else:
            result = self.use(messaging_fn("whatsapp_call"), to=to, video=video)
        if result.startswith("CALLING"):
            return f"Calling {to}."
        if result.startswith("CANCELLED"):
            return "Okay, I didn't call."
        return result.split(": ", 1)[-1]

    def call_control(self, m):
        text = m.group(0)
        action = "end" if re.search(r"end|hang|cut|disconnect|band|kaat|rakh", text) else (
            "camera" if "camera" in text else "mute")
        return self.use(messaging_fn("call_control"), action=action)

    # ---------- power ----------
    def lock(self, m):
        self.use(pc.lock_screen)
        return "Locking the PC."

    def power(self, m):
        word = m.group(1)
        action = {"shut down": "shutdown", "shutdown": "shutdown", "turn off": "shutdown", "power off": "shutdown",
                  "restart": "restart", "reboot": "restart", "sleep": "sleep", "log off": "logoff",
                  "sign out": "logoff"}[word]
        result = self.use(pc.power, action=action)
        return "Okay, cancelled." if "cancel" in result.lower() else f"{action.capitalize()} in a few seconds. Goodbye{self.title}."

    def run_command(self, m):
        result = self.use(pc.run_powershell, command=m.group(1))
        return result if len(result) < 200 else "Done. The output is on the dashboard."

    # ---------- fallback ----------
    def fallback(self, text):
        if re.match(r"(what|who|where|when|why|how|which|tell me|define|explain)\b", text):
            topic = re.sub(r"^(what|who|where|when|why|how|which)( is| are| was| were| does| do| did)?( a| an| the)? |^(tell me about|define|explain) ", "", text)
            answer = self.use(pc.wikipedia, topic=topic) if topic else ""
            if answer:
                return answer
            self.use(pc.google_search, query=text)
            return "I've searched Google for that."
        return f"Sorry{self.title}, I don't know how to do that yet. Say 'help' to hear what I can do."


RULES = [
    # power first, so "shut down" is never mistaken for something else
    (r"\b(shut down|shutdown|turn off|power off|restart|reboot|sleep|log off|sign out)\b.*\b(pc|computer|laptop|system|device)\b", "power"),
    (r"\block\b.*\b(pc|computer|laptop|screen|system)\b|^lock$", "lock"),
    (r"^run (?:the )?command (.+)", "run_command"),
    (r"^(?:search|find|open|show)(?: for)? (?:the )?(?:chat (?:with |of )?)?(.+?)(?:'s chat)? (?:on|in) (?:whatsapp|whats app)$", "open_chat"),
    (r"connect (?:to )?(?:my )?phone|^(?:open )?phone link$|link (?:my )?phone", "phone_link"),
    (r"\b(?:end|hang up|cut|disconnect)(?: the)? call\b|^hang up$|\bmute (?:the call|myself|me|my mic)\b|\b(?:turn (?:on|off) |switch (?:on|off) )?(?:the |my )?camera(?: on| off)?$", "call_control"),
    (r"^(?:please )?(?:make |start |place |do )?(?:an? )?(?:(?:whatsapp|whats app|phone|mobile|teams|video|voice|audio|normal)\s+)*(?:call|dial|ring)\b", "call"),
    (r"^(?:send|write|compose|draft|drop|shoot)\b.*\b(?:" + MESSAGE_APPS + r")\b", "message"),
    (r"^(?:message|msg|text|whatsapp|whats app|telegram|e-?mail|mail) (?!web$)(?!app$)\w", "message"),
    (r"^(hi|hello|hey|good morning|good afternoon|good evening|yo)\b", "greet"),
    (r"how are you|how's it going|how are things", "how_are_you"),
    (r"^(thanks|thank you|thank u|cheers|great job|well done)", "thanks"),
    (r"who are you|what are you|your name", "who_are_you"),
    (r"^help$|what can you do|your (features|commands|abilities)", "help"),
    (r"\bjoke\b", "joke"),
    (r"\bwhat(?:'s| is) the time|\bwhat time\b|^time$|current time", "time"),
    (r"\bwhat(?:'s| is) (?:the |today's )?date|what day is|today's date", "date"),
    (r"\bbattery\b", "battery"),
    (r"system status|\bcpu\b|\bram\b|\bmemory usage|how is my (pc|computer|laptop)", "status"),
    (r"\bweather\b(?: (?:in|at|for) ([a-z ]+))?|\btemperature (?:in|at) ([a-z ]+)", "weather"),
    (r"^remind me in (\d+(?:\.\d+)?) (seconds?|minutes?|mins?|hours?)(?: to | that | about )?(.*)", "remind"),
    (r"^set (?:a )?(?:timer|alarm|reminder) for (\d+(?:\.\d+)?) (seconds?|minutes?|mins?|hours?)()", "remind"),
    (r"^(take a note|note down|make a note|note|write down|remember)(?: that)?:? (.+)", "note"),
    (r"(?:take|capture) (?:a )?screenshot|screen ?shot", "screenshot"),
    (r"^type (.+)", "type_text"),
    (r"^press (.+)", "press"),
    (r"what(?:'s| is) (?:on |in )?my clipboard|read (?:my )?clipboard", "clipboard_read"),
    (r"minimi[sz]e (?:all|everything)|show (?:the )?desktop", "minimize_all"),
    (r"what(?:'s| is| are)? (?:open|running)$|which (?:apps|windows) are open|list (?:open )?windows", "windows"),
    (r"(?:set )?volume (?:to |at )?" + NUM + r"(?: percent|%)?$", "volume_set"),
    (r"(?:volume up|increase (?:the )?volume|turn (?:it|the volume) up|louder)(?: by " + NUM + r")?", "volume_up"),
    (r"(?:volume down|decrease (?:the )?volume|lower (?:the )?volume|turn (?:it|the volume) down|quieter)(?: by " + NUM + r")?", "volume_down"),
    (r"^(?:mute|unmute)\b", "mute"),
    (r"brightness (?:to |at )?" + NUM, "brightness"),
    (r"(increase|raise|decrease|lower|reduce|up|down) (?:the )?brightness|brightness (up|down)", "brightness_rel"),
    (r"^(?:(next|skip|previous|last) (?:song|track|video)|(pause|resume|stop)(?: (?:the )?(?:music|song|video|playback))?$|(play) (?:the )?(?:music|song)$)", "media"),
    (r"^(?:(?:play|search) )?(.+?) on youtube$|^youtube (.+)", "youtube"),
    (r"^play (.+)", "youtube"),
    (r"^(search for|google for|search|google|look up) (.+)", "search"),
    (r"^(?:show(?: me)? |list )?(?:the |all )?(?:files|items) (?:in|on) (?:my |the )?(desktop|downloads|documents|pictures|music|videos)", "list_folder"),
    (r"^(open|launch|start|run|go to) (.+)", "open_thing"),
    (r"^(close|quit|kill|exit) (.+)", "close_thing"),
    (r"^(who|what) (?:is|was|are|were) (.+)", "lookup"),
]
