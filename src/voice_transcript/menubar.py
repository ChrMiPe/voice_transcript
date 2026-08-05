import atexit
import json
import os
import subprocess
import sys
import threading
from datetime import datetime

import rumps

from voice_transcript.config import (
    HISTORY_FILE,
    SHORTCUTS_FILE,
    LLM_ENABLED,
    UV_PATH,
    load_settings,
    project_dir,
    save_settings,
)
from voice_transcript.hotkey import format_hotkey, register_hotkey
from voice_transcript.llm_server import (
    SOCKET_PATH,
    is_running as llm_is_running,
    stop_server as llm_stop_server,
)
from voice_transcript.main import dictate, load_history
from voice_transcript.notify import notify


def _asset_path(name):
    """Findet Assets sowohl im Dev- als auch im PyInstaller-Modus."""
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "assets")
    else:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "assets")
    return os.path.abspath(os.path.join(base, name))


ICON_IDLE = _asset_path("idleTemplate.png")
ICON_RECORDING = _asset_path("recordingTemplate.png")
ICON_PROCESSING = _asset_path("processingTemplate.png")
MENUBAR_PID_FILE = "/tmp/voice_transcript_menubar.pid"

ICONS = {"idle": ICON_IDLE, "recording": ICON_RECORDING, "processing": ICON_PROCESSING}
DICTATE_LABELS = {
    "idle": "Diktieren",
    "recording": "Aufnahme stoppen",
    "processing": "Verarbeitet…",
}

# Wie oft der LLM-Status im Menü nachgezogen wird (Sekunden). Der Check ist
# billig — PID-Datei lesen, Signal 0 senden, Socket-Existenz prüfen.
LLM_STATUS_INTERVAL = 5

HISTORY_MENU_ITEMS = 10
HISTORY_LABEL_CHARS = 60


def _format_time(timestamp):
    """Zeitstempel eines Historien-Eintrags als HH:MM."""
    if not timestamp:
        return "--:--"
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


class VoiceTranscriptApp(rumps.App):
    def __init__(self):
        super().__init__("Voice Transcript", icon=ICON_IDLE, template=True, quit_button=None)
        self.settings = load_settings()
        self._state = "idle"
        self._llm_process = None

        self.dictate_item = rumps.MenuItem(
            self._dictate_title(), callback=self.toggle_dictation
        )
        # Ohne callback zeichnet rumps den Eintrag ausgegraut — reine Statusanzeige.
        self.llm_status_item = rumps.MenuItem("LLM: Status wird geprüft…")

        self.menu = [
            self.dictate_item,
            rumps.separator,
            self.llm_status_item,
            rumps.separator,
            rumps.MenuItem("Historie"),
            rumps.separator,
            rumps.MenuItem("Hotkey ändern…", callback=self.change_hotkey),
            rumps.MenuItem("Shortcuts verwalten…", callback=self.manage_shortcuts),
            rumps.MenuItem("Config-Ordner öffnen", callback=self.open_config_dir),
            rumps.MenuItem("Historie löschen…", callback=self.clear_history),
            rumps.separator,
            rumps.MenuItem("Beenden", callback=rumps.quit_application, key="q"),
        ]

        self._refresh_history()
        self._write_pid()
        self._register_hotkey()
        self._start_llm_server()

        self._update_llm_status()
        self._llm_status_timer = rumps.Timer(self._update_llm_status, LLM_STATUS_INTERVAL)
        self._llm_status_timer.start()

    @property
    def recording(self):
        """Blockiert ein zweites Diktat — auch während noch verarbeitet wird."""
        return self._state in ("recording", "processing")

    def _dictate_title(self):
        hotkey_label = format_hotkey(
            self.settings["hotkey"]["key"],
            self.settings["hotkey"]["modifiers"],
        )
        return f"{DICTATE_LABELS[self._state]} ({hotkey_label})"

    def _set_state(self, state):
        """Zustand wechseln und Icon + Menü-Titel nachziehen."""
        self._state = state
        self.icon = ICONS[state]
        self.dictate_item.title = self._dictate_title()

    def _llm_status_title(self):
        if not LLM_ENABLED:
            return "LLM-Bereinigung aus"
        # Die PID-Datei schreibt der Server erst nach dem Modell-Laden, der Socket
        # kommt unmittelbar danach — daraus lässt sich "lädt noch" ableiten.
        if llm_is_running() and os.path.exists(SOCKET_PATH):
            return "✓ LLM bereit"
        if self._llm_process is not None and self._llm_process.poll() is None:
            return "◌ LLM lädt Modell…"
        return "⚠ LLM nicht erreichbar"

    def _update_llm_status(self, _=None):
        self.llm_status_item.title = self._llm_status_title()

    def _write_pid(self):
        with open(MENUBAR_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(self._cleanup)

    def _cleanup(self):
        llm_stop_server()
        if self._llm_process is not None:
            self._llm_process.terminate()
        if os.path.exists(MENUBAR_PID_FILE):
            os.remove(MENUBAR_PID_FILE)

    def _start_llm_server(self):
        """Startet den persistenten LLM-Server als separaten uv-Prozess."""
        if not LLM_ENABLED or llm_is_running():
            return

        notify("LLM", "Modell wird geladen...")
        try:
            self._llm_process = subprocess.Popen(
                [UV_PATH, "run", "--project", project_dir(),
                 "python", "-m", "voice_transcript.llm_server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._llm_process = None
            notify("Voice Transcript", "LLM-Server konnte nicht gestartet werden")

    def _register_hotkey(self):
        hk = self.settings["hotkey"]
        success = register_hotkey(hk["key"], hk["modifiers"], self.toggle_dictation)
        if not success:
            notify(
                "Voice Transcript",
                "Hotkey fehlgeschlagen — bitte Eingabeüberwachung für Voice Transcript erlauben",
            )

    def toggle_dictation(self, _=None):
        if self.recording:
            from voice_transcript.main import stop_dictation
            stop_dictation()
            return

        thread = threading.Thread(target=self._run_dictation, daemon=True)
        thread.start()

    def _run_dictation(self):
        # Sofort sperren, damit ein schneller zweiter Hotkey-Druck nicht ein
        # zweites Diktat startet, bevor dictate() den Zustand meldet.
        self._set_state("recording")

        def on_start():
            self._set_state("recording")

        def on_stop():
            self._set_state("processing")

        def on_result(text):
            self._set_state("idle")
            self._refresh_history()

        try:
            result = dictate(on_start=on_start, on_stop=on_stop, on_result=on_result)
            if result is None:
                self._set_state("idle")
        except Exception:
            self._set_state("idle")

    def _refresh_history(self):
        history_menu = self.menu["Historie"]

        # clear() greift direkt auf das NSMenu zu, das erst mit dem ersten add()
        # entsteht. Beim App-Start ist es None — früher brach die Methode hier
        # komplett ab, weshalb das Untermenü bis zum ersten Diktat leer blieb.
        if history_menu._menu is not None:
            history_menu.clear()

        history = load_history()
        if not history:
            history_menu.add(rumps.MenuItem("(leer)"))
            return

        for entry in history[:HISTORY_MENU_ITEMS]:
            text = entry["result"]
            label = text.replace("\n", " ")
            if len(label) > HISTORY_LABEL_CHARS:
                label = label[:HISTORY_LABEL_CHARS] + "…"
            item = rumps.MenuItem(
                f"{_format_time(entry.get('timestamp'))}  {label}",
                callback=self._copy_history_item,
            )
            item._history_text = text
            history_menu.add(item)

    def _copy_history_item(self, sender):
        text = getattr(sender, "_history_text", "")
        if text:
            cb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            cb.communicate(text.encode("utf-8"))
            notify("Diktat", "In Clipboard kopiert!")

    def change_hotkey(self, _):
        response = rumps.Window(
            title="Hotkey ändern",
            message=(
                "Format: modifier+modifier+taste\n"
                "Modifier: cmd, shift, alt, ctrl\n"
                "Beispiele: cmd+shift+d, cmd+alt+r, ctrl+shift+space"
            ),
            default_text="+".join(
                self.settings["hotkey"]["modifiers"]
                + [self.settings["hotkey"]["key"]]
            ),
            ok="Speichern",
            cancel="Abbrechen",
        ).run()

        if response.clicked:
            parts = [p.strip().lower() for p in response.text.split("+")]
            if len(parts) < 2:
                notify("Fehler", "Mindestens ein Modifier + Taste")
                return

            key = parts[-1]
            modifiers = parts[:-1]

            self.settings["hotkey"] = {"key": key, "modifiers": modifiers}
            save_settings(self.settings)
            self._register_hotkey()

            self.dictate_item.title = self._dictate_title()
            notify("Voice Transcript", f"Neuer Hotkey: {format_hotkey(key, modifiers)}")

    def manage_shortcuts(self, _):
        shortcuts = {}
        if os.path.exists(SHORTCUTS_FILE):
            with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
                shortcuts = json.load(f)

        # Aktuelle Shortcuts als lesbaren Text
        current = "\n".join(f"{k} → {v}" for k, v in shortcuts.items())
        if not current:
            current = "(keine Shortcuts vorhanden)"

        response = rumps.Window(
            title="Shortcuts verwalten",
            message=(
                f"Aktuelle Shortcuts:\n{current}\n\n"
                "Neuen Shortcut hinzufügen oder bestehenden ändern.\n"
                "Trigger-Wort eingeben (z.B. 'chris email'):"
            ),
            default_text="",
            ok="Weiter",
            cancel="Schließen",
        ).run()

        if not response.clicked or not response.text.strip():
            return

        trigger = response.text.strip().lower()

        # Ersetzungstext abfragen
        current_value = shortcuts.get(trigger, "")
        response2 = rumps.Window(
            title=f"Shortcut: {trigger}",
            message=f"Ersetzungstext für '{trigger}':\n(leer lassen zum Löschen)",
            default_text=current_value,
            ok="Speichern",
            cancel="Abbrechen",
        ).run()

        if not response2.clicked:
            return

        if response2.text.strip():
            shortcuts[trigger] = response2.text.strip()
            notify("Shortcuts", f"'{trigger}' → '{response2.text.strip()}'")
        else:
            shortcuts.pop(trigger, None)
            notify("Shortcuts", f"'{trigger}' gelöscht")

        with open(SHORTCUTS_FILE, "w", encoding="utf-8") as f:
            json.dump(shortcuts, f, ensure_ascii=False, indent=2)

    def open_config_dir(self, _):
        from voice_transcript.config import APP_SUPPORT_DIR
        subprocess.run(["open", APP_SUPPORT_DIR])

    def clear_history(self, _):
        count = len(load_history())
        if not count:
            notify("Diktat", "Historie ist bereits leer")
            return

        confirmed = rumps.alert(
            title="Historie löschen?",
            message=f"{count} gespeicherte Diktate werden endgültig entfernt.",
            ok="Löschen",
            cancel="Abbrechen",
        )
        if not confirmed:
            return

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        self._refresh_history()
        notify("Diktat", "Historie gelöscht")


def _is_already_running():
    """Prüft ob bereits eine Instanz läuft (anhand der PID-Datei)."""
    if not os.path.exists(MENUBAR_PID_FILE):
        return False
    try:
        with open(MENUBAR_PID_FILE, "r") as f:
            pid = int(f.read().strip())
        # Prüfe ob der Prozess noch lebt
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        # Prozess existiert nicht mehr — PID-Datei aufräumen
        os.remove(MENUBAR_PID_FILE)
        return False


def main():
    if _is_already_running():
        return
    VoiceTranscriptApp().run()


if __name__ == "__main__":
    main()
