import atexit
import json
import os
import subprocess
import sys
import threading

import rumps

from voice_transcript.config import (
    HISTORY_FILE,
    SHORTCUTS_FILE,
    LLM_ENABLED,
    load_settings,
    save_settings,
)
from voice_transcript.hotkey import format_hotkey, register_hotkey
from voice_transcript.llm_server import is_running as llm_is_running, stop_server as llm_stop_server
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


class VoiceTranscriptApp(rumps.App):
    def __init__(self):
        super().__init__("Voice Transcript", icon=ICON_IDLE, template=True, quit_button=None)
        self.recording = False
        self.settings = load_settings()

        hotkey_label = format_hotkey(
            self.settings["hotkey"]["key"],
            self.settings["hotkey"]["modifiers"],
        )

        self.dictate_item = rumps.MenuItem(
            f"Diktieren ({hotkey_label})", callback=self.toggle_dictation
        )

        self.menu = [
            self.dictate_item,
            rumps.separator,
            rumps.MenuItem("Historie"),
            rumps.separator,
            rumps.MenuItem("Hotkey ändern", callback=self.change_hotkey),
            rumps.MenuItem("Shortcuts verwalten", callback=self.manage_shortcuts),
            rumps.MenuItem("Config-Ordner öffnen", callback=self.open_config_dir),
            rumps.MenuItem("Historie löschen", callback=self.clear_history),
            rumps.separator,
            rumps.MenuItem("Beenden", callback=rumps.quit_application, key="q"),
        ]

        self._refresh_history()
        self._write_pid()
        self._register_hotkey()
        self._start_llm_server()

    def _write_pid(self):
        with open(MENUBAR_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(self._cleanup)

    def _cleanup(self):
        llm_stop_server()
        if hasattr(self, "_llm_process") and self._llm_process:
            self._llm_process.terminate()
        if os.path.exists(MENUBAR_PID_FILE):
            os.remove(MENUBAR_PID_FILE)

    def _start_llm_server(self):
        """Startet den persistenten LLM-Server als separaten uv-Prozess."""
        if not LLM_ENABLED or llm_is_running():
            return

        notify("LLM", "Modell wird geladen...")
        project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if getattr(sys, "frozen", False):
            # PyInstaller-Modus: Projekt-Verzeichnis aus Config ableiten
            project_dir = os.path.expanduser("~/projects/voice_transcript")

        uv_path = "/Library/Frameworks/Python.framework/Versions/3.13/bin/uv"
        try:
            self._llm_process = subprocess.Popen(
                [uv_path, "run", "--project", project_dir,
                 "python", "-m", "voice_transcript.llm_server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
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
        self.recording = True

        def on_start():
            self.icon = ICON_RECORDING

        def on_stop():
            self.icon = ICON_PROCESSING

        def on_result(text):
            self.icon = ICON_IDLE
            self.recording = False
            self._refresh_history()

        try:
            result = dictate(on_start=on_start, on_stop=on_stop, on_result=on_result)
            if result is None:
                self.icon = ICON_IDLE
                self.recording = False
        except Exception:
            self.icon = ICON_IDLE
            self.recording = False

    def _refresh_history(self):
        history_menu = self.menu["Historie"]
        if history_menu._menu is None:
            return

        history_menu.clear()

        history = load_history()
        if not history:
            history_menu.add(rumps.MenuItem("(leer)"))
            return

        for entry in history[:10]:
            text = entry["result"]
            label = text[:60] + "..." if len(text) > 60 else text
            label = label.replace("\n", " ")
            item = rumps.MenuItem(label, callback=self._copy_history_item)
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

            hotkey_label = format_hotkey(key, modifiers)
            self.dictate_item.title = f"Diktieren ({hotkey_label})"
            notify("Voice Transcript", f"Neuer Hotkey: {hotkey_label}")

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
