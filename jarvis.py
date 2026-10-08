"""JARVIS - a voice assistant that controls your PC, with a live dashboard at http://localhost:8765

Usage:
    python jarvis.py              # voice mode: say "Jarvis, open notepad"
    python jarvis.py --text       # no microphone: type in the dashboard or this terminal
    python jarvis.py --mute       # don't speak replies
    python jarvis.py --no-browser # don't open the dashboard automatically
"""

import argparse
import os
import queue
import re
import threading
import time
import webbrowser
from pathlib import Path

import pc_tools
import server
from brain import Brain
from events import bus
from voice import Voice

WAKE_WORDS = ("jarvis", "jervis", "javis", "jarvi", "jarwis", "travis", "jarvish", "charvis", "harvis", "garvis", "jar vis", "jaavis", "jarves")  # common mishearings included
EXIT_WORDS = {"goodbye", "bye", "exit", "quit"}
FOLLOW_UP_SECONDS = 8  # after Jarvis answers, you can talk without the wake word for this long


def parse_yes_no(text):
    lower = text.lower()
    if re.search(r"\b(no|nope|cancel|stop|don't|dont|nahi|nahin|mat|ruko)\b", lower):
        return False
    if re.search(r"\b(yes|yeah|yep|sure|go ahead|do it|send it|send|call|confirm|ok|okay|haan|haa|ha|han|ji|bhej do|kar do|karo|theek|thik|chalo|lagao|yup)\b", lower):
        return True
    return None


def load_env():
    env = Path(__file__).with_name(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


class Jarvis:
    def __init__(self, text_mode=False, muted=False):
        self.text_mode = text_mode
        self.voice = Voice(muted=muted)
        self.brain = Brain()
        self.commands = queue.Queue()
        self.busy = threading.Event()
        self.paused = threading.Event()
        self.running = True
        self.follow_up_until = 0.0
        self.wake_required = os.environ.get("JARVIS_WAKE_WORD", "on").lower() != "off"
        self.pending = None  # an open yes/no question
        self.pending_ask = None  # an open free-text question
        pc_tools.confirm = self.confirm
        pc_tools.ask = self.ask
        pc_tools.say = self.speak
        bus.set_state(brain=self.brain.name, stt="text only" if text_mode else "-", wake_word=self.wake_required)

    # ---------- shared helpers ----------
    def speak(self, text):
        bus.emit("chat", role="jarvis", text=text)
        self.voice.say(text)

    def submit(self, text, source):
        # While Jarvis is waiting on a question, typed text answers it instead of starting a new command.
        if self.pending_ask:
            self.pending_ask["text"] = text
            self.pending_ask["event"].set()
            return
        if self.pending:
            yes = parse_yes_no(text)
            if yes is not None:
                bus.emit("chat", role="user", text=text, source=source)
                self.answer_confirm(yes)
            return
        self.commands.put((text, source))

    def control(self, action):
        if action == "pause":
            self.paused.set()
            bus.set_state(status="paused")
        elif action == "resume":
            self.paused.clear()
            bus.set_state(status="idle")
        elif action == "stop_speaking":
            self.voice.stop_speaking()
        elif action == "toggle_wake":
            self.wake_required = not self.wake_required
            bus.set_state(wake_word=self.wake_required)
            bus.log("Wake word " + ("ON: start commands with 'Jarvis'" if self.wake_required else "OFF: everything you say is a command"))
        elif action == "listen":  # mic button: talk without the wake word
            self.paused.clear()
            self.follow_up_until = time.time() + FOLLOW_UP_SECONDS
            bus.set_state(status="listening", awake=True)

    def answer_confirm(self, yes):
        if self.pending:
            self.pending["answer"] = yes
            self.pending["event"].set()

    def _hear_reply(self, done, timeout):
        """Listen (mic) until `done` is set from the dashboard or a reply is heard. Returns text or ''."""
        deadline = time.time() + timeout
        while time.time() < deadline and not done.is_set():
            if self.text_mode or self.paused.is_set():
                done.wait(0.2)
                continue
            bus.set_state(status="listening")
            heard, engine = self.voice.listen(wait_timeout=3, should_abort=done.is_set)
            if heard:
                bus.emit("heard", text=heard, engine=engine, wake=True)
                return heard
        return ""

    def ask(self, question, timeout=40):
        """Ask a free-text question (e.g. 'What should the message say?'). Answer by voice or typing."""
        done = threading.Event()
        self.pending_ask = {"event": done, "text": None}
        bus.emit("ask", question=question)
        self.speak(question)
        heard = self._hear_reply(done, timeout)
        answer = (self.pending_ask["text"] or heard or "").strip()
        self.pending_ask = None
        bus.emit("ask_done")
        if answer:
            print(f"YOU (answer): {answer}")
            bus.emit("chat", role="user", text=answer, source="answer")
        if re.fullmatch(r"(cancel|stop|never ?mind|rehne do|chhodo)\W*", answer.lower()):
            return ""
        bus.set_state(status="thinking")
        return answer

    def confirm(self, question, details=None, timeout=45):
        """Ask yes/no. Answer by voice, typing yes/no, or the dashboard buttons. Silence means no."""
        done = threading.Event()
        self.pending = {"event": done, "answer": None}
        bus.emit("confirm", question=question, details=details or {})
        self.speak(question)
        deadline = time.time() + timeout
        while time.time() < deadline and not done.is_set():
            heard = self._hear_reply(done, deadline - time.time())
            if not heard:
                break
            yes = parse_yes_no(heard)
            if yes is None:
                self.speak("Please say yes or no.")
                continue
            self.pending["answer"] = yes
            break
        answer = self.pending["answer"] is True
        self.pending = None
        bus.emit("confirm_done", answer=answer)
        bus.set_state(status="thinking")
        return answer

    # ---------- command worker ----------
    def worker(self):
        while self.running:
            text, source = self.commands.get()
            self.busy.set()
            self.voice.stop_speaking()
            print(f"YOU ({source}): {text}")
            bus.emit("chat", role="user", text=text, source=source)
            words = set(re.findall(r"[a-z']+", text.lower()))
            if len(words) <= 4 and words & EXIT_WORDS:
                self.speak("Goodbye. Powering down.")
                self.running = False
                os._exit(0)
            bus.set_state(status="thinking")
            try:
                reply = self.brain.handle(text)
            except Exception as e:
                bus.log(f"Brain error: {e}", "error")
                reply = "Sorry, something went wrong with that command."
            self.speak(reply)
            self.follow_up_until = time.time() + FOLLOW_UP_SECONDS
            bus.set_state(status="paused" if self.paused.is_set() else "idle")
            self.busy.clear()

    # ---------- microphone loop ----------
    def listen_loop(self):
        self.voice.calibrate()
        while self.running:
            if self.paused.is_set() or self.busy.is_set():
                time.sleep(0.1)
                continue
            awake = time.time() < self.follow_up_until or not self.wake_required
            bus.set_state(status="listening", awake=awake)
            heard, engine = self.voice.listen(
                wait_timeout=4, should_abort=lambda: self.busy.is_set() or self.paused.is_set())
            if self.busy.is_set() or self.paused.is_set():
                continue
            bus.set_state(status="listening")
            if not heard:
                continue
            lower = heard.lower()
            wake = next((w for w in WAKE_WORDS if re.search(rf"\b{w}\b", lower)), None)
            awake = awake or time.time() < self.follow_up_until + 2 or not self.wake_required
            bus.emit("heard", text=heard, engine=engine, wake=bool(wake or awake))
            if not wake and not awake:
                continue
            command = heard
            if wake:
                command = heard[lower.index(wake) + len(wake):].strip(" ,.!?")
                if not command:
                    self.speak("Yes?")
                    self.follow_up_until = time.time() + FOLLOW_UP_SECONDS
                    continue
            self.submit(command, "voice")

    def terminal_loop(self):
        while self.running:
            try:
                text = input().strip()
            except EOFError:  # no terminal attached: keep serving the dashboard
                while self.running:
                    time.sleep(1)
                return
            if text:
                self.submit(text, "typed")

    def run(self, open_browser=True):
        url = server.start(self)
        print(f"\n  JARVIS dashboard: {url}\n")
        if open_browser:
            webbrowser.open(url)
        threading.Thread(target=self.worker, daemon=True).start()
        title = os.environ.get("JARVIS_USER_TITLE", "").strip()
        self.speak(f"JARVIS online{', ' + title if title else ''}. How can I help?")
        if self.text_mode:
            bus.set_state(status="idle")
            print("Type commands here or in the dashboard. Type 'exit' to quit.")
            self.terminal_loop()
        else:
            print('Say "Jarvis" followed by a command. Say "goodbye Jarvis" to exit.')
            self.listen_loop()


def main():
    parser = argparse.ArgumentParser(description="JARVIS voice assistant")
    parser.add_argument("--text", action="store_true", help="don't use the microphone")
    parser.add_argument("--mute", action="store_true", help="don't speak replies")
    parser.add_argument("--no-browser", action="store_true", help="don't open the dashboard")
    args = parser.parse_args()
    load_env()
    try:
        Jarvis(text_mode=args.text, muted=args.mute).run(open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print("\nJARVIS offline.")


if __name__ == "__main__":
    main()
