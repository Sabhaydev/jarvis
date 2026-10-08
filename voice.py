"""Ears (speech-to-text) and voice (text-to-speech) for Jarvis.

Speech-to-text: Fish Audio ASR -> Google (free) fallback.
Text-to-speech: Fish Audio (your voice model) -> ElevenLabs -> Windows built-in voice.
Every engine that fails with a billing/auth error is switched off for the rest of the session.
"""

import io
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np
import pyttsx3
import sounddevice as sd
import speech_recognition as sr

from events import bus

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.1
DANIEL_VOICE = "onwK4e9ZLuTAKqWW03F9"  # free ElevenLabs premade voice: calm British male


class Voice:
    def __init__(self, muted=False):
        self.language = os.environ.get("JARVIS_LANGUAGE", "en-IN")
        self.muted = muted
        self.recognizer = sr.Recognizer()
        self.noise_floor = None
        self.disabled = set()  # engines turned off after billing/auth errors
        self._eleven_voice = None
        self._tts_lock = threading.Lock()
        self._stop = threading.Event()

    # =============== text-to-speech ===============
    def say(self, text):
        print(f"\nJARVIS: {text}\n")
        if self.muted or not text:
            return
        with self._tts_lock:
            self._stop.clear()
            previous = bus.state["status"]
            bus.set_state(status="speaking")
            try:
                for name, engine in (("Fish Audio", self._fish), ("ElevenLabs", self._elevenlabs),
                                     ("Windows voice", self._windows)):
                    if name in self.disabled:
                        continue
                    bus.set_state(tts=name)
                    if engine(text):
                        return
            finally:
                bus.set_state(status="idle" if previous == "speaking" else previous)

    def stop_speaking(self):
        self._stop.set()
        sd.stop()

    def _stream_pcm(self, response, rate):
        """Play raw 16-bit mono PCM as it downloads, so speech starts quickly."""
        leftover = b""
        with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16") as out:
            while not self._stop.is_set():
                chunk = response.read(4096)
                if not chunk:
                    break
                chunk = leftover + chunk
                cut = len(chunk) - (len(chunk) % 2)
                out.write(chunk[:cut])
                leftover = chunk[cut:]

    def _http_tts(self, name, request, rate):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._stream_pcm(response, rate)
            return True
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            if e.code in (401, 402, 403):
                self.disabled.add(name)
                bus.log(f"{name} voice unavailable ({e.code}): {detail} - switching engine", "warn")
            else:
                bus.log(f"{name} error {e.code}: {detail}", "warn")
        except Exception as e:
            bus.log(f"{name} unreachable: {e}", "warn")
        return False

    def _fish(self, text):
        key = os.environ.get("FISH_API_KEY")
        if not key:
            return False
        body = {"text": text, "format": "pcm", "sample_rate": 24000, "latency": "balanced"}
        if os.environ.get("FISH_VOICE_ID"):
            body["reference_id"] = os.environ["FISH_VOICE_ID"]
        request = urllib.request.Request(
            "https://api.fish.audio/v1/tts", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "model": os.environ.get("FISH_TTS_MODEL", "s2.1-pro-free")})
        return self._http_tts("Fish Audio", request, 24000)

    def _elevenlabs(self, text):
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            return False
        voice_id = self._eleven_voice or os.environ.get("ELEVENLABS_VOICE_ID") or DANIEL_VOICE
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_22050",
            data=json.dumps({"text": text, "model_id": "eleven_flash_v2_5"}).encode(),
            headers={"xi-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._stream_pcm(response, 22050)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 402 and voice_id != DANIEL_VOICE:
                bus.log("ElevenLabs voice needs a paid plan - using the free 'Daniel' voice", "warn")
                self._eleven_voice = DANIEL_VOICE
                return self._elevenlabs(text)
            detail = e.read().decode(errors="replace")[:200]
            if e.code in (401, 402, 403):
                self.disabled.add("ElevenLabs")
            bus.log(f"ElevenLabs error {e.code}: {detail}", "warn")
        except Exception as e:
            bus.log(f"ElevenLabs unreachable: {e}", "warn")
        return False

    def _windows(self, text):
        try:
            # A fresh engine per utterance avoids pyttsx3's "run loop already started" hangs.
            engine = pyttsx3.init()
            engine.setProperty("rate", 185)
            male = [v for v in engine.getProperty("voices") if "david" in v.name.lower()]
            if male:
                engine.setProperty("voice", male[0].id)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            return True
        except Exception as e:
            bus.log(f"Windows voice error: {e}", "error")
            return False

    # =============== speech-to-text ===============
    def calibrate(self, seconds=1.0):
        bus.log("Calibrating microphone - stay quiet for a second")
        audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        blocks = audio.astype(np.float32).reshape(-1, int(SAMPLE_RATE * BLOCK_SECONDS))
        rms = float(np.percentile(np.sqrt(np.mean(blocks ** 2, axis=1)), 30))
        self.noise_floor = min(max(rms, 50.0), 400.0)
        bus.log(f"Microphone noise floor: {self.noise_floor:.0f}")

    def record(self, wait_timeout=8.0, silence_after=1.0, max_seconds=15.0, should_abort=None):
        """Record one utterance. Returns raw int16 bytes, or None if nobody spoke."""
        if self.noise_floor is None:
            self.calibrate()
        threshold = self.noise_floor * 1.8 + 120
        block = int(BLOCK_SECONDS * SAMPLE_RATE)
        frames, started, silent_blocks, waited, pre_roll = [], False, 0, 0.0, []

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block) as stream:
            while True:
                data, _ = stream.read(block)
                level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                if not started:
                    if should_abort and should_abort():
                        return None
                    pre_roll = (pre_roll + [data.copy()])[-8:]  # keep ~0.8s before speech so 'Jarvis' isn't cut
                    if level > threshold:
                        started = True
                        frames.extend(pre_roll)
                        bus.set_state(status="hearing")
                    else:
                        waited += BLOCK_SECONDS
                        if waited >= wait_timeout:
                            return None
                    continue
                frames.append(data.copy())
                silent_blocks = silent_blocks + 1 if level < threshold else 0
                if silent_blocks * BLOCK_SECONDS >= silence_after or len(frames) * BLOCK_SECONDS >= max_seconds:
                    break
        return np.concatenate(frames).tobytes()

    def transcribe(self, raw):
        if os.environ.get("FISH_API_KEY") and "Fish ASR" not in self.disabled:
            bus.set_state(stt="Fish Audio ASR")
            text = self._fish_asr(raw)
            if text is not None:
                return text, "Fish Audio ASR"
        bus.set_state(stt="Google (free)")
        try:
            return self.recognizer.recognize_google(sr.AudioData(raw, SAMPLE_RATE, 2), language=self.language), "Google"
        except sr.UnknownValueError:
            return "", "Google"
        except sr.RequestError as e:
            bus.log(f"Google speech recognition unavailable: {e}", "error")
            return "", "Google"

    def _fish_asr(self, raw):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(raw)
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{self.language[:2]}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="speech.wav"\r\n'
                f"Content-Type: audio/wav\r\n\r\n").encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            "https://api.fish.audio/v1/asr", data=body,
            headers={"Authorization": f"Bearer {os.environ['FISH_API_KEY']}", "model": "transcribe-1-pro",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = json.load(response).get("text", "")
            return " ".join(part.split("|>")[-1] for part in text.split("<|")).strip()  # drop speaker tags
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:160]
            if e.code in (401, 402, 403):
                self.disabled.add("Fish ASR")
                bus.log(f"Fish Audio speech-to-text unavailable ({e.code}): {detail} - using Google instead", "warn")
            else:
                bus.log(f"Fish ASR error {e.code}: {detail}", "warn")
        except Exception as e:
            bus.log(f"Fish ASR unreachable: {e}", "warn")
        return None

    def listen(self, wait_timeout=8.0, should_abort=None):
        """Listen for one utterance; returns (text, engine). text is '' if nothing understood."""
        raw = self.record(wait_timeout=wait_timeout, should_abort=should_abort)
        if raw is None:
            return "", None
        return self.transcribe(raw)
