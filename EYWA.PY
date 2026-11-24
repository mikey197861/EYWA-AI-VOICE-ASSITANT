import os, io, re, time, wave, asyncio, hashlib, tempfile, subprocess, webbrowser, urllib.parse
from collections import deque
import numpy as np, sounddevice as sd, pygame, pyautogui, edge_tts
from groq import Groq

# ====== PUT YOUR KEY HERE ======
GROQ_KEY = "YOUR API KEY"
# ===============================

# ====== YOUR CONTACTS (lowercase name : number with country code, no + or spaces) ======
CONTACTS = {
    "mom": "919999999999",
    "rahul": "919888888888",
}
# ========================================================================================

# ====== VOICE (keep your own pitch/rate if you changed them) ======
VOICE = "en-GB-MaisieNeural"
PITCH = "+20Hz"
RATE = "+8%"
# ==================================================================

# ====== BEHAVIOUR ======
ANSWER_QUESTIONS_WITHOUT_NAME = True   # "what is H2O" works without saying Eywa
CONVERSATION_SECONDS = 30              # keeps listening for follow-ups after each reply
# =======================

NAME = "Eywa"
WAKE_RE = re.compile(r"\b(eywa|eiwa|ewa|eva|aiwa|aywa|eyewa|iwa|ayuwa|ewah|eywah)\b")
QUESTION_RE = re.compile(
    r"^(what|whats|what's|who|whom|whose|why|how|when|where|which|is|are|was|were|can|could|"
    r"do|does|did|will|would|should|explain|define|describe|tell me|give me|calculate|convert|"
    r"translate|meaning of|difference between|full form of|capital of|solve)\b")
HALLUCINATIONS = {"thank you", "thanks", "thank you.", "thanks for watching", "you", "bye", "okay", "ok"}

CHAT_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]       # smart first, fast as backup
STT_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]       # speech to text

client = Groq(api_key=GROQ_KEY)
SR = 16000
history = []
THRESH = 0.01
active_until = 0.0

# ---------- VOICE (always Maisie, cached for speed) ----------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _make_audio(text):
    key = hashlib.md5(f"{VOICE}{PITCH}{RATE}{text}".encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key + ".mp3")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    last = None
    for _ in range(3):
        try:
            asyncio.run(edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save(path))
            return path
        except Exception as e:
            last = e
            if os.path.exists(path):
                os.remove(path)
            time.sleep(0.5)
    print("Voice error:", last)
    return None

def speak(text):
    print(f"{NAME}:", text)
    path = _make_audio(text)
    if path is None:
        print("(voice unavailable, check your internet)")
        return
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(50)
    pygame.mixer.music.unload()

# ---------- LISTENING ----------
def calibrate():
    global THRESH
    rec = sd.rec(SR, samplerate=SR, channels=1, dtype="float32")
    sd.wait()
    noise = float(np.abs(rec).mean())
    THRESH = max(0.008, noise * 3)
    print(f"Noise level {noise:.4f}, threshold {THRESH:.4f}")

def transcribe(audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    data = buf.getvalue()
    names = ", ".join(CONTACTS.keys())
    prompt = (f"Eywa, open Edge, close Edge, open Notepad, search for, YouTube, Gmail, shutdown, cancel, "
              f"answer call, end call, call {names}, send message to {names} saying, yes, no, "
              f"what is H2O, explain, difference between.")
    for model in STT_MODELS:
        try:
            text = client.audio.transcriptions.create(
                file=("audio.wav", data),
                model=model,
                language="en",
                prompt=prompt,
                response_format="text",
            )
            return str(text).strip().lower()
        except Exception as e:
            print(f"Speech error with {model}:", e)
    return ""

def listen(max_sec=10, silence_sec=1.0):
    block = int(SR * 0.1)
    pre = deque(maxlen=4)
    frames, silent, started = [], 0.0, False
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=block) as s:
        for _ in range(int(max_sec / 0.1)):
            data, _ = s.read(block)
            loud = np.abs(data).mean() > THRESH
            if not started:
                pre.append(data)
                if loud:
                    started = True
                    frames.extend(pre)
            else:
                frames.append(data)
                silent = 0.0 if loud else silent + 0.1
                if silent >= silence_sec:
                    break
    if not frames:
        return ""
    audio = np.concatenate(frames).flatten()
    if len(audio) < SR * 0.4:
        return ""
    return transcribe(audio)

# ---------- COMMANDS ----------
APPS = {
    "edge": ("start msedge", "msedge.exe"),
    "browser": ("start msedge", "msedge.exe"),
    "firefox": ("start firefox", "firefox.exe"),
    "chrome": ("start chrome", "chrome.exe"),
    "notepad": ("start notepad", "notepad.exe"),
    "calculator": ("start calc", "CalculatorApp.exe"),
    "paint": ("start mspaint", "mspaint.exe"),
    "task manager": ("start taskmgr", "Taskmgr.exe"),
    "command prompt": ("start cmd", "cmd.exe"),
    "settings": ("start ms-settings:", "SystemSettings.exe"),
    "word": ("start winword", "WINWORD.EXE"),
    "excel": ("start excel", "EXCEL.EXE"),
    "vs code": ("start code", "Code.exe"),
    "spotify": ("start spotify", "Spotify.exe"),
    "whatsapp": ("start whatsapp:", "WhatsApp.exe"),
}
SITES = {
    "youtube": "https://youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "google": "https://google.com",
    "instagram": "https://instagram.com",
    "whatsapp web": "https://web.whatsapp.com",
}

def adb(*args):
    return subprocess.run(["adb", "shell", *args], capture_output=True, text=True)

def send_whatsapp(name, number, msg):
    speak(f"Send to {name}: {msg}. Say yes to confirm.")
    answer = listen(max_sec=6)
    print("Confirm heard:", answer)
    if not any(w in answer for w in ("yes", "yeah", "send", "confirm")):
        speak("Okay, cancelled.")
        return
    url = f"whatsapp://send?phone={number}&text={urllib.parse.quote(msg)}"
    os.startfile(url)
    time.sleep(6)
    pyautogui.press("enter")
    speak("Message sent.")

def handle(cmd):
    # time and date (answered locally, instantly)
    if re.search(r"\b(what time is it|what's the time|what is the time|time now|current time|tell me the time)\b", cmd):
        speak("It is " + time.strftime("%I:%M %p").lstrip("0") + ".")
        return True
    if re.search(r"\b(today's date|the date today|what is the date|what's the date|what day is it|what day is today|date today)\b", cmd):
        speak("Today is " + time.strftime("%A, %d %B %Y") + ".")
        return True

    # shutdown
    if "shutdown" in cmd or "shut down" in cmd:
        speak("Shutting down in 10 seconds. Say cancel to stop.")
        subprocess.run("shutdown /s /t 10", shell=True)
        if "cancel" in listen(max_sec=6):
            subprocess.run("shutdown /a", shell=True)
            speak("Shutdown cancelled.")
        return True

    # send message: "send message to mom saying I will be late"
    if re.search(r"\b(send|text|message)\b", cmd):
        for name, number in CONTACTS.items():
            if re.search(rf"\bto {re.escape(name)}\b", cmd):
                m = re.search(r"\b(?:saying|say|that)\b\s+(.+)", cmd)
                if not m:
                    speak("What should I say?")
                    msg = listen(max_sec=10)
                else:
                    msg = m.group(1)
                msg = msg.strip(" .")
                if not msg:
                    speak("I did not catch the message.")
                    return True
                send_whatsapp(name, number, msg[0].upper() + msg[1:])
                return True

    # phone calls via Android + adb
    if "answer" in cmd and "call" in cmd:
        adb("input", "keyevent", "5")
        speak("Answering.")
        return True
    if ("end" in cmd or "hang up" in cmd or "decline" in cmd or "reject" in cmd) and "call" in cmd:
        adb("input", "keyevent", "6")
        speak("Call ended.")
        return True
    if re.search(r"\b(call|phone|ring)\b", cmd):
        for name, number in CONTACTS.items():
            if re.search(rf"\b{re.escape(name)}\b", cmd):
                adb("am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:+{number}")
                speak(f"Calling {name}.")
                return True

    closing = re.search(r"\b(close|quit)\b", cmd)
    opening = re.search(r"\b(open|start|launch)\b", cmd)

    if closing or opening:
        for name, (open_cmd, exe) in APPS.items():
            if re.search(rf"\b{re.escape(name)}\b", cmd):
                if closing:
                    subprocess.run(f"taskkill /im {exe} /f", shell=True)
                    speak(f"Closed {name}.")
                else:
                    subprocess.run(open_cmd, shell=True)
                    speak(f"Opening {name}.")
                return True

    if opening:
        for name, url in SITES.items():
            if name in cmd:
                webbrowser.open_new_tab(url)
                speak(f"Opening {name}.")
                return True

    if cmd.startswith(("search for", "google")):
        q = cmd.replace("search for", "").replace("google", "").strip()
        webbrowser.open_new_tab("https://www.google.com/search?q=" + urllib.parse.quote(q))
        speak(f"Searching {q}.")
        return True
    return False

# ---------- AI ANSWERS ----------
def chat(text):
    system = {"role": "system", "content": (
        f"You are {NAME}, a soft, sweet and friendly voice assistant. "
        f"Today is {time.strftime('%A, %d %B %Y')}. "
        "Your answers are spoken aloud, so use plain sentences only: no markdown, no lists, no symbols. "
        "Answer questions clearly and correctly in 1 to 3 short sentences. "
        "If you do not know something or it needs live information, say so honestly.")}
    history.append({"role": "user", "content": text})
    reply = None
    for model in CHAT_MODELS:
        try:
            extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
            r = client.chat.completions.create(
                model=model,
                messages=[system] + history[-8:],
                max_tokens=700,          # room for reasoning + a short spoken answer
                extra_body=extra,
            )
            reply = (r.choices[0].message.content or "").strip()
            if reply:
                break
        except Exception as e:
            print(f"Groq error with {model}:", e)
    if not reply:
        reply = "Sorry, I could not reach the AI."
    history.append({"role": "assistant", "content": reply})
    speak(reply)

# ---------- MAIN LOOP ----------
def main():
    global active_until
    speak("Calibrating, stay quiet for a second.")
    calibrate()
    speak(f"Hi! {NAME} is ready.")
    while True:
        heard = listen()
        if not heard:
            continue
        print("Heard:", heard)
        if heard.strip(" ,.!?") in HALLUCINATIONS:
            print("(ignored: probably background noise)")
            continue
        m = WAKE_RE.search(heard)
        if m:
            cmd = heard[m.end():].strip(" ,.!?")
        else:
            cmd = heard.strip(" ,.!?")
        print("Command:", cmd)
        if m and not cmd:
            speak("Yes?")
            cmd = listen()
            print("Command:", cmd)
            if not cmd:
                continue
        if cmd in ("exit", "stop listening", "goodbye"):
            speak("Bye bye!")
            break

        handled = handle(cmd)
        print("Handled:", handled)
        if handled:
            active_until = time.time() + CONVERSATION_SECONDS
            continue

        in_conversation = time.time() < active_until
        is_question = bool(QUESTION_RE.match(cmd)) and ANSWER_QUESTIONS_WITHOUT_NAME
        if m or in_conversation or is_question:
            chat(cmd)
            active_until = time.time() + CONVERSATION_SECONDS
        else:
            print("(ignored: no wake word and not a question)")

if __name__ == "__main__":
    main()