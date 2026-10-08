# JARVIS: voice assistant for your PC

Say **"Jarvis, ..."** and it does things on your Windows PC. A live dashboard at **http://127.0.0.1:8765** shows the conversation, what the mic heard, which engines are in use, every action Jarvis takes, and your PC's stats.

No paid AI is needed. The brain is a built-in command engine that runs on your PC.

| Part | Engine (falls back to the next one automatically) |
|---|---|
| Brain | Built-in command engine (`brain.py`), offline and free |
| Speech to text | Fish Audio ASR (needs API credit), then Google (free) |
| Voice | Fish Audio with your voice model, then ElevenLabs, then the Windows built-in voice |
| Knowledge | Wikipedia (free) and wttr.in weather (free) |

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys. Then double-click `start_jarvis.bat`, or run `python jarvis.py`.

| Option | What it does |
|---|---|
| `--text` | No microphone. Type commands in the dashboard or terminal |
| `--mute` | Don't speak replies |
| `--no-browser` | Don't open the dashboard automatically |

## Things you can say

- **Apps and websites:** "open Chrome", "close Notepad", "open YouTube", "open downloads folder", "what's open"
- **Music:** "play Believer on YouTube", "play music", "pause", "next song"
- **Volume and screen:** "volume up", "set volume to 40", "mute", "brightness 70", "show desktop"
- **Info:** "what's the time", "today's date", "weather in Delhi", "who is APJ Abdul Kalam", "what is a black hole", "battery", "system status"
- **Productivity:** "remind me in 10 minutes to drink water", "set a timer for 5 minutes", "note buy milk" (saved to `jarvis_notes.txt` on your Desktop), "take a screenshot", "files in downloads"
- **Keyboard:** "type hello world", "press control plus s", "press alt tab"
- **Power:** "lock the PC", "shut down the computer", "restart the computer" (asks you first)
- **Search:** "search for python tutorials"
- **Other:** "help", "tell me a joke", "goodbye Jarvis"

After Jarvis answers, you have about 8 seconds to say a follow-up without "Jarvis". The mic button on the dashboard does the same.

## Messages and email (always asks before sending)

- "send a WhatsApp message to Rahul saying I'll be late"
- "whatsapp papa I reached home" / "Rahul ko WhatsApp pe message bhejo ki main late ho jaunga"
- "send a Telegram message to Aman saying hi"
- "email boss about leave saying I'm not feeling well" (Gmail on the web)
- "compose an email on Outlook to HR about salary slip saying please share it" (Outlook app)
- "send a message to Priya": Jarvis asks for anything missing, like the message text or the email address.

Jarvis opens the app and types the draft into it, then shows the draft on the dashboard and reads it out. It only sends when you say or click **yes**. If you say **no**, a chat draft is cleared and an email draft is left open for you to edit.

**Contacts:** copy `contacts.example.json` to `contacts.json` and add phone numbers and emails. With a phone number, WhatsApp opens the right chat directly. Without one, Jarvis searches your chats by name. Email addresses you spell out ("rahul at gmail dot com") are saved there automatically.

## Calls (always asks before calling)

- "call Rahul", "video call mom", "call papa on WhatsApp", "Rahul ko call karo", "mummy ko video call karo": **WhatsApp** voice or video call (needs the WhatsApp app)
- "phone call to Rahul", "dial 98765 43210", "papa ko phone pe call karo": a normal **phone call through Phone Link** (your phone must be linked)
- "call boss on Teams": **Microsoft Teams** call to their email
- During a call: "mute the call", "turn off the camera", "hang up" / "call kaato"

Jarvis opens the chat or gets the number, then asks you. Nothing is dialled until you say or click yes. Phone numbers you say are saved to `contacts.json`. The first phone call may make Windows ask which app should open phone links. Pick **Phone Link**.

## App or web

"open WhatsApp / Instagram / Teams / Discord / Telegram / Spotify / Netflix…" opens the **installed app** if it's on your PC, and otherwise its **website**. Add "web" to force the browser: "open Instagram web".

## Adding new commands

Open `brain.py`. Add a method to `Brain`, then add a `(regex, "method_name")` line to `RULES`. PC actions live in `pc_tools.py`.

## Files

- `jarvis.py`: main program (mic loop, wake word, command worker)
- `brain.py`: understands commands
- `pc_tools.py`: actions on the PC
- `voice.py`: speech to text and text to speech, with fallbacks
- `server.py` and `ui.html`: the local dashboard
- `events.py`: live event stream behind the dashboard
