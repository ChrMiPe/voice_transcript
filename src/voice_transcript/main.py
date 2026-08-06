import json
import os
import signal
import subprocess
import time
from datetime import datetime

from voice_transcript import asr, permissions
from voice_transcript.applog import log
from voice_transcript.cleanup import clean_german_text
from voice_transcript.glossary import correct as correct_glossary
from voice_transcript.config import HISTORY_FILE, MAX_HISTORY, PID_FILE
from voice_transcript.llm import llm_polish
from voice_transcript.notify import notify
from voice_transcript.shortcuts import apply_shortcuts

# UTF-8 sicherstellen (PyInstaller-Apps haben oft kein LANG gesetzt)
os.environ.setdefault("LANG", "de_DE.UTF-8")
os.environ.setdefault("LC_ALL", "de_DE.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Jeder Hilfsprozess im Diktat-Pfad braucht eine Obergrenze. Blieb einer haengen
# — System Events klemmt nach Schlaf oder Abmeldung gern —, blieb der Zustand auf
# "processing" stehen und die recording-Property sperrte jedes weitere Diktat aus.
# Die einzige Ausnahme ist `yap dictate` selbst: dessen Laufzeit bestimmt der
# Nutzer, indem er den Hotkey erneut drueckt.
OSASCRIPT_TIMEOUT = 10
CLIPBOARD_TIMEOUT = 5


def save_to_history(raw, result):
    history = load_history()

    history.insert(0, {
        "raw": raw,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    })
    history = history[:MAX_HISTORY]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_history():
    """Historie lesen. Bei Schaden eine leere Liste statt einer Ausnahme.

    Wichtig, weil _refresh_history() schon in VoiceTranscriptApp.__init__ laeuft:
    eine kaputte history.json hat die App gar nicht starten lassen.
    """
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            daten = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Historie nicht lesbar: {type(e).__name__}: {e}")
        return []
    if not isinstance(daten, list):
        log("Historie hat unerwartetes Format — erwartet wird eine Liste")
        return []
    # Eintraege ohne "result" wuerden die Menue- und Panel-Anzeige zerlegen.
    return [e for e in daten if isinstance(e, dict) and "result" in e]


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _running_pid():
    """PID der laufenden yap-Aufnahme, oder None.

    Prueft, ob der Prozess ueberhaupt noch lebt: nach einem Absturz bleibt die
    PID-Datei liegen, und die alte Fassung meldete jede vorhandene Datei als
    "laeuft". Der naechste Hotkey-Druck hat dann nur diese Leiche gestoppt statt
    aufzunehmen — erst der zweite Druck startete ein Diktat.
    """
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        _clear_pid()
        return None

    try:
        os.kill(pid, 0)
    except OSError:
        log(f"Verwaiste PID-Datei aufgeraeumt (PID {pid})")
        _clear_pid()
        return None

    # Lebt da wirklich yap? PIDs werden wiederverwendet, und ein SIGINT an einen
    # fremden Prozess ist schlimmer als ein verschluckter Hotkey-Druck.
    if not _ist_yap(pid):
        log(f"PID {pid} gehoert nicht mehr yap — Datei aufgeraeumt")
        _clear_pid()
        return None

    return pid


def _ist_yap(pid):
    """Prueft den Prozessnamen. Bei Zweifel False — lieber nichts signalisieren."""
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=CLIPBOARD_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Prozessname zu PID {pid} nicht ermittelbar: {e}")
        return False
    return os.path.basename((res.stdout or "").strip()) == "yap"


def stop_dictation():
    """Stoppt eine laufende Aufnahme. True, wenn eine lief.

    Zwei Wege, weil die Engines unterschiedlich aufnehmen: Whisper nimmt im eigenen
    Prozess auf und wird direkt gestoppt, `yap` laeuft als Subprozess und bekommt
    SIGINT. Der PID-Weg bleibt zusaetzlich, damit ein *anderer* Prozess (CLI,
    Raycast) eine yap-Aufnahme beenden kann.
    """
    if asr.stop_active():
        _clear_pid()
        return True

    pid = _running_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGINT)
    except OSError as e:
        log(f"SIGINT an yap (PID {pid}) fehlgeschlagen: {e}")
    finally:
        _clear_pid()
    return True


def dictate(on_start=None, on_stop=None, on_polish=None, on_result=None):
    """Fuehrt ein Diktat durch. Callbacks fuer UI-Updates.

    on_stop meldet das Ende der Aufnahme (danach laeuft die Erkennung), on_polish
    den Beginn der LLM-Bereinigung. Zwei Phasen, weil beide merklich dauern und ein
    einziges "wird verarbeitet" nichts darueber sagt, wie weit es ist.
    """
    if stop_dictation():
        if on_stop:
            on_stop()
        return None

    if on_start:
        on_start()
    notify("Yap Dictation", "Starte Diktat...")
    subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])

    try:
        session, benutzt = asr.start()
        if session is None:
            log(f"Aufnahme nicht startbar: {benutzt}")
            notify("Fehler", str(benutzt)[:120])
            return None

        raw_text, fehler = asr.finish(session)

        if on_stop:
            on_stop()

        if fehler or not raw_text:
            # Ohne Log war „nichts gesagt" nicht von „Mikrofon verweigert" zu
            # unterscheiden — beides sah nach leerem Ergebnis aus.
            log(f"Spracherkennung ({benutzt}) ohne Ergebnis: {fehler or '—'}")
            notify("Spracherkennung", str(fehler or "Kein Text erkannt").splitlines()[0][:120])
            return None

        # Pipeline: Glossar -> Shortcuts -> Verzoegerungslaute -> LLM.
        # Das Glossar laeuft zuerst: danach koennen Shortcuts Adressen und URLs
        # einsetzen, die der phonetische Abgleich sonst wieder verbiegen wuerde.
        text = correct_glossary(raw_text)
        text = apply_shortcuts(text)
        text = clean_german_text(text)

        if on_polish:
            on_polish()
        text = llm_polish(text)

        # Vor dem Ueberschreiben sichern, damit ein Diktat nicht die Zwischenablage
        # des Nutzers verbraucht.
        previous_clipboard = _read_clipboard()

        write_clipboard(text.encode("utf-8"))
        save_to_history(raw_text, text)

        if _paste_at_cursor():
            _restore_clipboard(previous_clipboard)
        # Bei Fehlschlag bleibt der diktierte Text liegen — dann ist ⌘V die
        # Rettung und darf nicht weggeraeumt werden.

        if on_result:
            on_result(text)

        return text

    except Exception as e:
        log(f"Diktat fehlgeschlagen: {type(e).__name__}: {e}")
        notify("Fehler", str(e))
        if on_stop:
            on_stop()
        return None
    finally:
        _clear_pid()


PASTE_SCRIPT = 'tell application "System Events" to key code 9 using command down'

# Zeit, die die Ziel-App braucht, um das ⌘V zu verarbeiten. osascript kehrt
# zurueck, sobald System Events das Tastenereignis abgeschickt hat — die Ziel-App
# liest die Zwischenablage erst danach. Wird hier zu kurz gewartet, stellen wir
# den alten Inhalt wieder her, bevor er ausgelesen wurde.
PASTE_SETTLE_SECONDS = 0.5


def write_clipboard(data):
    """Schreibt Bytes in die Zwischenablage. True bei Erfolg."""
    try:
        cb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        cb.communicate(data, timeout=CLIPBOARD_TIMEOUT)
        return cb.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log(f"pbcopy fehlgeschlagen: {type(e).__name__}: {e}")
        return False


def _read_clipboard():
    """Aktuellen Zwischenablage-Inhalt als Bytes, oder None.

    pbpaste liefert nur Klartext. Bild- oder Rich-Content kommt als leerer bzw.
    unbrauchbarer Wert zurueck — den wuerden wir beim Wiederherstellen durch
    Nichts ersetzen und damit mehr kaputt machen als reparieren. Deshalb gilt
    nur nicht-leerer Text als sicherungswuerdig.
    """
    try:
        res = subprocess.run(["pbpaste"], capture_output=True, timeout=CLIPBOARD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"pbpaste fehlgeschlagen: {type(e).__name__}: {e}")
        return None

    if res.returncode != 0 or not res.stdout:
        return None
    return res.stdout


def _restore_clipboard(data):
    """Stellt den gesicherten Inhalt wieder her.

    Der diktierte Text ist ueber "Letzte Diktate" weiter erreichbar, es geht also
    nichts verloren.
    """
    if not data:
        return
    time.sleep(PASTE_SETTLE_SECONDS)
    write_clipboard(data)


def _paste_at_cursor():
    """Fuegt den Clipboard-Inhalt am Cursor ein (⌘V).

    Erst einfuegen, dann bei Misserfolg nach dem Grund fragen — nicht umgekehrt.
    AXIsProcessTrusted liefert innerhalb eines laufenden Prozesses einen
    gecachten Wert: erteilt man die Bedienungshilfen bei laufender App, bleibt
    die Antwort "nein", bis die App neu startet. Ein Vorab-Check haette das
    Einfuegen also auch dann verhindert, wenn es funktioniert haette.
    """
    time.sleep(0.2)

    try:
        res = subprocess.run(
            ["osascript", "-e", PASTE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Ohne Timeout blieb der Diktat-Thread hier haengen, wenn System Events
        # klemmt — und mit ihm der Zustand "processing", der jedes weitere
        # Diktat aussperrt.
        log(f"Einfuegen abgebrochen — osascript nach {OSASCRIPT_TIMEOUT}s ohne Antwort")
        notify("Text im Clipboard", "Einfügen hängt — bitte ⌘V drücken")
        return False
    except OSError as e:
        log(f"Einfuegen fehlgeschlagen: {type(e).__name__}: {e}")
        notify("Text im Clipboard", "Einfügen nicht möglich — bitte ⌘V drücken")
        return False

    if res.returncode == 0:
        notify("Diktat", "Text eingefuegt!")
        subprocess.Popen(["afplay", "/System/Library/Sounds/Purr.aiff"])
        return True

    detail = (res.stderr or "").strip().replace("\n", " ")
    trusted = permissions.is_trusted()
    log(f"Einfuegen fehlgeschlagen (exit {res.returncode}, trusted={trusted}): {detail or '—'}")

    if not trusted:
        notify("Text im Clipboard", "Bedienungshilfen fehlen — bitte ⌘V drücken")
    else:
        notify("Text im Clipboard", "Einfügen abgelehnt — bitte ⌘V drücken")
    return False


def run_dictation():
    """Standalone-Aufruf (Raycast/CLI)."""
    dictate()
