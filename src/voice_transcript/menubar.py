import atexit
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import rumps
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplicationWillTerminateNotification,
    NSBezelBorder,
    NSColor,
    NSFont,
    NSFontWeightRegular,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakeRect,
    NSMakeSize,
    NSScrollView,
    NSTextView,
    NSViewWidthSizable,
)
from Foundation import NSNotificationCenter, NSObject, NSOperationQueue, NSThread

from voice_transcript import asr, glossary, permissions
from voice_transcript.applog import log
from voice_transcript.config import (
    GLOSSARY_FILE,
    HISTORY_FILE,
    MODEL_IDLE_CHOICES,
    SHORTCUTS_FILE,
    UV_PATH,
    llm_enabled,
    load_settings,
    model_idle_timeout,
    project_dir,
    push_to_talk,
    save_settings,
)
from voice_transcript.hotkey import format_hotkey, register_hotkey
from voice_transcript.llm_server import (
    SOCKET_PATH,
    is_running as llm_is_running,
    stop_server as llm_stop_server,
)
from voice_transcript.main import dictate, load_history, write_clipboard
from voice_transcript.notify import notify
from voice_transcript.panel import Panel, attach as attach_panel


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

ICONS = {"idle": ICON_IDLE, "recording": ICON_RECORDING,
         "transcribing": ICON_PROCESSING, "processing": ICON_PROCESSING}
# Seit Whisper dauert die Verarbeitung merklich: ein 5-Minuten-Diktat sind rund 19 s
# Transkription plus bis zu 100 s Bereinigung. Ein einziges "Wird verarbeitet…" laesst
# den Nutzer raten, ob noch etwas passiert.
DICTATE_LABELS = {
    "idle": "Diktieren",
    "recording": "Aufnahme stoppen",
    "transcribing": "Wird erkannt…",
    "processing": "Wird bereinigt…",
}

# SF Symbols statt der gezeichneten 22px-PNGs: Vektoren, scharf in jeder Groesse.
# Der Aufnahme-Zustand wird eingefaerbt — die PNGs enthielten zwar einen roten
# Punkt, doch bei template=True verwirft macOS jede Farbe und zeichnet nur die
# Alpha-Maske. Das Rot war also nie zu sehen.
SYMBOLS = {"idle": "mic", "recording": "mic.fill",
           "transcribing": "waveform", "processing": "waveform"}
COLORED_STATES = ("recording",)
SYMBOL_POINT_SIZE = 15
_SYMBOL_SCALE_MEDIUM = 2  # NSImageSymbolScaleMedium


def _symbol_image(name, colored):
    """SF-Symbol als NSImage, oder None wenn das System es nicht kennt."""
    if not hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
        return None
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None

    config = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
        SYMBOL_POINT_SIZE, NSFontWeightRegular, _SYMBOL_SCALE_MEDIUM
    )
    if colored:
        # Ein gefaerbtes Symbol kommt als template=False zurueck — nur so
        # uebersteht das Rot das monochrome Rendering der Menueleiste.
        config = config.configurationByApplyingConfiguration_(
            NSImageSymbolConfiguration.configurationWithHierarchicalColor_(
                NSColor.systemRedColor()
            )
        )
    return image.imageWithSymbolConfiguration_(config)


# Einmal beim Import aufbauen; None-Werte fallen spaeter auf die PNGs zurueck.
SYMBOL_IMAGES = {
    state: _symbol_image(name, state in COLORED_STATES)
    for state, name in SYMBOLS.items()
}

# Wie oft die Status-Zeilen im Menü nachgezogen werden (Sekunden). Beide Checks
# sind billig — PID-Datei lesen, Signal 0 senden, Socket-Existenz prüfen bzw.
# AXIsProcessTrusted abfragen.
STATUS_INTERVAL = 5

# Auch `open` laeuft ueber LaunchServices und kann klemmen. Der Aufruf sitzt im
# Menue-Callback, also auf dem Main-Thread — ohne Grenze friert die Menueleiste ein.
OPEN_TIMEOUT = 10

HISTORY_MENU_ITEMS = 10
HISTORY_LABEL_CHARS = 45

# Wartezeit, bis rumps das Statusitem gebaut hat (Sekunden).
PANEL_SETUP_INTERVAL = 0.2

# Rücknahme für den Server-Neustart. Ohne sie versuchte der 5-Sekunden-Timer es
# gemessen 12x pro Minute — samt Benachrichtigung je Versuch, also 720 pro Stunde,
# wenn der Server prinzipiell nicht hochkommt (uv fehlt, Repo verschoben).
LLM_RESTART_INTERVAL = 60
LLM_RESTART_MAX = 3

# Takt fuer die laufenden Sekunden im Panel. Laeuft nur, solange aufgenommen wird
# *und* das Panel offen ist — ein Dauertimer fuer eine Anzeige, die meist niemand
# sieht, waere Verschwendung.
ELAPSED_INTERVAL = 1
# Nur eine Obergrenze gegen absurd lange Zeilen — die eigentliche Kuerzung macht
# das Label selbst (setLineBreakMode_), damit *eine* Stelle entscheidet und nicht
# zwei Ellipsen entstehen.
PANEL_LABEL_CHARS = 90


def _text_dialog(titel, hinweis, text, ok="Speichern", cancel="Abbrechen",
                 groesse=(420, 260)):
    """Mehrzeiliger Dialog mit *scrollbarem* Textfeld. Rueckgabe: Text oder None.

    rumps.Window benutzt ein NSTextField. Das umbricht, scrollt aber nicht — bei
    einer langen Begriffsliste kommt man an das Ende nicht heran. Hier steht deshalb
    eine NSTextView in einer NSScrollView.

    Automatische Ersetzungen sind ausgeschaltet, und das ist keine Kosmetik: die
    Anfuehrungszeichen-Automatik macht aus geraden krumme, und die
    Rechtschreibkorrektur verbiegt genau die Fachbegriffe, um die es hier geht.
    """
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, *groesse))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(False)
    scroll.setBorderType_(NSBezelBorder)

    view = NSTextView.alloc().initWithFrame_(
        NSMakeRect(0, 0, groesse[0], groesse[1])
    )
    view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, NSFontWeightRegular))
    view.setRichText_(False)
    view.setAutomaticQuoteSubstitutionEnabled_(False)
    view.setAutomaticDashSubstitutionEnabled_(False)
    view.setAutomaticSpellingCorrectionEnabled_(False)
    view.setAutomaticTextReplacementEnabled_(False)
    view.setString_(text)
    view.setMinSize_(NSMakeSize(0, groesse[1]))
    view.setVerticallyResizable_(True)
    view.setHorizontallyResizable_(False)
    view.setAutoresizingMask_(NSViewWidthSizable)
    view.textContainer().setWidthTracksTextView_(True)
    scroll.setDocumentView_(view)

    alert = NSAlert.alloc().init()
    alert.setMessageText_(titel)
    alert.setInformativeText_(hinweis)
    alert.setAlertStyle_(0)
    alert.addButtonWithTitle_(ok)
    if cancel:
        alert.addButtonWithTitle_(cancel)
    alert.setAccessoryView_(scroll)
    # Ohne das landet der Fokus auf der Taste und man muss erst ins Feld klicken.
    alert.window().setInitialFirstResponder_(view)

    if alert.runModal() != NSAlertFirstButtonReturn:
        return None
    return str(view.string())


class _TerminationObserver(NSObject):
    """Ruft eine Aufraeumfunktion, wenn AppKit den Prozess beendet.

    Noetig, weil atexit auf diesem Weg nicht feuert — siehe _write_pid. Die
    Benachrichtigung kommt auf jedem AppKit-Weg: der Menueeintrag „Beenden“, das
    Abmelden, ein Quit-Apple-Event.
    """

    def terminating_(self, _notification):
        try:
            self.on_terminate()
        except Exception as e:
            log(f"Aufraeumen beim Beenden fehlgeschlagen: {type(e).__name__}: {e}")


def make_termination_observer(on_terminate):
    """Modulfunktion, weil PyObjC jede Methode einer ObjC-Subklasse als Selektor
    deutet und eine Fabrik mit Argument zu keinem passt (wie panel.make_router)."""
    observer = _TerminationObserver.alloc().init()
    observer.on_terminate = on_terminate
    return observer


def _on_main(fn):
    """Fuehrt fn auf dem Main-Thread aus.

    Das Diktat laeuft in einem Worker-Thread, AppKit ist aber nicht threadsicher —
    Icon- und Menue-Aenderungen von dort konnten die App haengen lassen.
    """
    if NSThread.isMainThread():
        fn()
    else:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


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
        self._dictation_thread = None
        # Vor allem anderen: _refresh_history() und _update_status() laufen noch in
        # __init__ und ziehen das Panel mit — ohne diese Zuweisung schlaegt das mit
        # AttributeError fehl und die App startet gar nicht.
        self._panel = None
        self._panel_router = None
        self._llm_restart_versuche = 0
        self._llm_restart_zuletzt = 0.0
        self._phase_started = None
        self._elapsed_timer = None
        self._cleaned_up = False
        self._terminate_observer = None

        # super() hat gerade das PNG gesetzt — jetzt das SF-Symbol darueberlegen.
        # initializeStatusBar() liest _icon_nsimage beim Launch, der frueh gesetzte
        # Wert bleibt also erhalten; ohne das hier erschiene das Symbol erst beim
        # ersten Zustandswechsel.
        self._apply_icon()

        self.dictate_item = rumps.MenuItem(
            self._dictate_title(), callback=self.toggle_dictation
        )
        # Ohne callback zeichnet rumps den Eintrag ausgegraut — reine Statusanzeige.
        self.llm_status_item = rumps.MenuItem("LLM: Status wird geprüft…")
        # Anklickbar: fehlende Bedienungshilfen sind der haeufigste Grund, warum
        # der Text nur im Clipboard landet — von hier aus direkt reparierbar.
        self.access_status_item = rumps.MenuItem(
            "Einfügen: Status wird geprüft…", callback=self.fix_accessibility
        )
        # Umschalter als Auswahl mit Haken statt als Satz. „Erkennung: Whisper → auf
        # Apple Speech" musste man zweimal lesen, um zu wissen, was gilt *und* was
        # der Klick tut. Ein Haken sagt beides auf einen Blick.
        self.engine_items = {
            "whisper": rumps.MenuItem("Whisper", callback=self._pick_engine),
            "yap": rumps.MenuItem("Apple Speech", callback=self._pick_engine),
        }
        for wert, item in self.engine_items.items():
            item._wert = wert

        self.ptt_items = {
            False: rumps.MenuItem("Zweimal drücken", callback=self._pick_ptt),
            True: rumps.MenuItem("Halten (Push-to-talk)", callback=self._pick_ptt),
        }
        for wert, item in self.ptt_items.items():
            item._wert = wert

        # Der Entlade-Zeitpunkt war bis hierher nur per Hand in settings.json
        # erreichbar — und er entscheidet, wann 4,49 GB freigegeben werden.
        self.idle_items = {}
        for sekunden, text in MODEL_IDLE_CHOICES:
            item = rumps.MenuItem(text, callback=self._pick_idle)
            item._wert = sekunden
            self.idle_items[sekunden] = item

        self.llm_item = rumps.MenuItem("LLM-Bereinigung", callback=self.toggle_llm)

        # Diktieren steht allein oben; alles, was man selten braucht, liegt unter
        # „Einstellungen". Die Statuszeilen bilden eine leise Fusszeile.
        self.menu = [
            self.dictate_item,
            rumps.separator,
            rumps.MenuItem("Letzte Diktate"),
            [
                rumps.MenuItem("Einstellungen"),
                [
                    [rumps.MenuItem("Erkennung"), list(self.engine_items.values())],
                    [rumps.MenuItem("Hotkey"), list(self.ptt_items.values()) + [
                        rumps.separator,
                        rumps.MenuItem("Kombination ändern…", callback=self.change_hotkey),
                    ]],
                    [rumps.MenuItem("Modelle im Speicher"), list(self.idle_items.values()) + [
                        rumps.separator,
                        rumps.MenuItem("jetzt entladen", callback=self.unload_models),
                    ]],
                    rumps.separator,
                    self.llm_item,
                    rumps.separator,
                    rumps.MenuItem("Fachbegriffe verwalten…", callback=self.manage_glossary),
                    rumps.MenuItem("Shortcuts verwalten…", callback=self.manage_shortcuts),
                    rumps.separator,
                    rumps.MenuItem("Config-Ordner öffnen", callback=self.open_config_dir),
                    rumps.MenuItem("Historie löschen…", callback=self.clear_history),
                ],
            ],
            rumps.separator,
            self.llm_status_item,
            self.access_status_item,
            rumps.MenuItem("Beenden", callback=rumps.quit_application, key="q"),
        ]
        self._update_settings_marks()

        self._refresh_history()
        self._write_pid()
        self._register_hotkey()
        self._check_accessibility_on_launch()
        self._start_llm_server()

        self._update_status()
        self._status_timer = rumps.Timer(self._update_status, STATUS_INTERVAL)
        self._status_timer.start()

        # Das Statusitem entsteht erst in rumps' applicationDidFinishLaunching,
        # also nach __init__. Ein kurzer Timer wartet darauf und haengt dann das
        # Panel an; danach schaltet er sich ab.
        self._panel_timer = rumps.Timer(self._install_panel, PANEL_SETUP_INTERVAL)
        self._panel_timer.start()

    @property
    def recording(self):
        """Blockiert ein zweites Diktat — auch während noch verarbeitet wird."""
        return self._state in ("recording", "transcribing", "processing")

    def _dictate_title(self):
        hotkey_label = format_hotkey(
            self.settings["hotkey"]["key"],
            self.settings["hotkey"]["modifiers"],
        )
        return f"{DICTATE_LABELS[self._state]} ({hotkey_label})"

    def panel_elapsed(self):
        """Dauer der laufenden Phase als M:SS, oder None.

        Waehrend der Aufnahme die Aufnahmedauer, danach die Wartezeit. Genau darum
        geht es: bei einem fuenfminuetigen Diktat sind das rund 19 s Erkennung plus
        bis zu 100 s Bereinigung, und ohne mitlaufende Zahl weiss man nicht, ob noch
        etwas passiert. Der Zaehler laeuft ueber Erkennung und Bereinigung hinweg
        weiter — gefragt ist "wie lange warte ich schon", nicht "wie lange dauert
        dieser Teilschritt".
        """
        if self._phase_started is None:
            return None
        if self._state not in ("recording", "transcribing", "processing"):
            return None
        sekunden = int(time.monotonic() - self._phase_started)
        return f"{sekunden // 60}:{sekunden % 60:02d}"

    def _set_state(self, state):
        """Zustand wechseln und Icon + Menü-Titel nachziehen.

        Der Zustand selbst wird sofort gesetzt — der Hotkey-Handler liest ihn als
        Sperre gegen ein zweites Diktat und darf nicht auf den Main-Thread warten.
        Nur die AppKit-Zugriffe wandern dorthin.
        """
        # Neu anlaufen lassen bei Aufnahmebeginn und beim Uebergang in die
        # Verarbeitung; von "transcribing" nach "processing" laeuft er weiter.
        if state in ("recording", "transcribing") and self._state != state:
            self._phase_started = time.monotonic()
        self._state = state
        _on_main(self._apply_state)

    def _apply_state(self):
        self._apply_icon()
        self.dictate_item.title = self._dictate_title()
        self._sync_elapsed_timer()
        self._refresh_panel_status()

    def _sync_elapsed_timer(self):
        """Sekundentakt nur waehrend der Aufnahme.

        Der Timer aktualisiert die Dauer im Panel. Ausserhalb der Aufnahme gibt es
        nichts zu zaehlen, also laeuft er dann auch nicht.
        """
        # Bewusst *ohne* Pruefung, ob das Panel offen ist: _tick_elapsed ruft nur
        # refresh_status_if_open, das bei geschlossenem Panel sofort zurueckkehrt.
        # Ein Aufruf pro Sekunde ist billiger als die Kopplung, die noetig waere,
        # um den Timer beim Oeffnen und Schliessen mitzufuehren — und ein Timer, der
        # dabei haengenbleibt, waere der schlimmere Fehler.
        soll = self._state in ("recording", "transcribing", "processing")
        if soll and self._elapsed_timer is None:
            self._elapsed_timer = rumps.Timer(self._tick_elapsed, ELAPSED_INTERVAL)
            self._elapsed_timer.start()
        elif not soll and self._elapsed_timer is not None:
            self._elapsed_timer.stop()
            self._elapsed_timer = None

    def _tick_elapsed(self, _=None):
        self._refresh_panel_status()

    def _apply_icon(self):
        image = SYMBOL_IMAGES.get(self._state)
        if image is None:
            self.icon = ICONS[self._state]  # PNG-Fallback
            return

        # rumps' icon-Setter laedt ausschliesslich aus Dateien, deshalb die NSImage
        # direkt hinterlegen. setStatusBarIcon() liest genau dieses Attribut — auch
        # initializeStatusBar() beim Launch, ein frueher Aufruf geht also nicht verloren.
        self._icon_nsimage = image
        try:
            self._nsapp.setStatusBarIcon()
        except AttributeError:
            pass  # noch kein Statusitem — der Launch zeichnet es selbst

    def _llm_status_title(self):
        # Symbol nur, wenn etwas zu tun ist — sonst wird die Fusszeile zum Jahrmarkt.
        if not llm_enabled():
            return "LLM-Bereinigung aus"
        # Die PID-Datei schreibt der Server erst nach dem Modell-Laden, der Socket
        # kommt unmittelbar danach — daraus lässt sich "lädt noch" ableiten.
        if llm_is_running() and os.path.exists(SOCKET_PATH):
            # Ob die Modelle gerade im Speicher liegen, weiss nur der Server.
            zustand = asr.model_status()
            if zustand and not any(zustand.get("loaded", {}).values()):
                return "LLM bereit — Modelle entladen"
            return "LLM bereit"
        if self._llm_process is not None and self._llm_process.poll() is None:
            return "LLM lädt Modell…"
        return "⚠ LLM nicht erreichbar"

    def _update_status(self, _=None):
        self._restart_llm_server_if_dead()
        self.llm_status_item.title = self._llm_status_title()
        self._refresh_panel_status()

        # Sind die Bedienungshilfen erteilt, gibt es nichts zu melden — dann bleibt
        # die Zeile ausgeblendet statt einen Haken zu zeigen, den niemand braucht.
        granted = permissions.is_trusted()
        if not granted:
            self.access_status_item.title = "⚠ Bedienungshilfen fehlen — klicken"
        self.access_status_item._menuitem.setHidden_(granted)

    # ─── Popover-Panel ───

    def _install_panel(self, _=None):
        """Haengt das Panel an das Statusitem, sobald es existiert.

        Scheitert das, bleibt das Menue am Statusitem haengen und die App
        funktioniert wie vorher — ein Panel ist kein Grund, die Menueleiste zu
        verlieren.
        """
        status_item = getattr(self._nsapp, "nsstatusitem", None)
        if status_item is None:
            return  # rumps ist noch nicht fertig, naechster Timer-Durchlauf

        self._panel_timer.stop()
        try:
            button, self._panel_router = attach_panel(status_item, self._status_clicked)
            self._panel = Panel(self, button)
            log("Panel am Statusitem installiert")
        except Exception as e:
            log(f"Panel nicht installierbar, bleibe beim Menue: {type(e).__name__}: {e}")
            self._panel = None
            self._panel_router = None
            status_item.setMenu_(self._menu._menu)

    def _status_clicked(self, right):
        if right or self._panel is None:
            self._show_menu()
            return

        # Laesst sich das Panel nicht zeigen, darf der Klick nicht ins Leere gehen —
        # dann kommt das Menue, wie vor der Umstellung.
        if not self._panel.toggle():
            log("Panel nicht sichtbar — zeige stattdessen das Menue")
            self._show_menu()

    def _show_menu(self):
        """Zeigt das klassische Menue beim Rechtsklick.

        Das Menue muss dafuer kurz zurueck an das Statusitem — nur dann zeichnet
        macOS es an der richtigen Stelle. Danach wieder abklemmen, sonst faengt es
        den naechsten Linksklick wieder ab.
        """
        # Haken auffrischen: eine von Hand geaenderte settings.json soll sich nicht
        # als veralteter Haken zeigen.
        self._update_settings_marks()
        status_item = self._nsapp.nsstatusitem
        status_item.setMenu_(self._menu._menu)
        status_item.button().performClick_(None)
        status_item.setMenu_(None)

    def _refresh_panel_status(self):
        if self._panel is not None:
            self._panel.refresh_status_if_open()

    def _refresh_panel_history(self):
        if self._panel is not None:
            self._panel.refresh_history_if_open()

    # ─── Delegate fuer panel.py ───

    def panel_state(self):
        return self._state

    def panel_dictate_label(self):
        return DICTATE_LABELS[self._state]

    def panel_hotkey_label(self):
        return format_hotkey(
            self.settings["hotkey"]["key"], self.settings["hotkey"]["modifiers"]
        )

    def panel_history(self):
        entries = []
        for entry in load_history()[:HISTORY_MENU_ITEMS]:
            text = entry["result"]
            label = text.replace("\n", " ")
            if len(label) > PANEL_LABEL_CHARS:
                label = label[:PANEL_LABEL_CHARS] + "…"
            entries.append({
                "time": _format_time(entry.get("timestamp")),
                "label": label,
                "text": text,
            })
        return entries

    def panel_status(self):
        """Die eine Statuszeile: Punkt + Text links, Engine rechts.

        Frueher standen hier drei Zeilen — LLM-Status, Bedienungshilfen-Warnung und
        ein dauerhafter Rechtsklick-Hinweis — fuer 60 pt Hoehe. Die Warnung ist die
        einzige, die ein Handeln verlangt, also verdraengt sie den LLM-Status; die
        Engine passt daneben.
        """
        beschreibung = {"whisper": "Whisper", "yap": "Apple Speech"}
        rechts = beschreibung.get(asr.engine(), asr.engine())

        if not permissions.is_trusted():
            return {
                "text": "Bedienungshilfen fehlen — klicken",
                "warn": True,
                "action": "accessibility",
                "right": rechts,
            }

        titel = self._llm_status_title()
        return {"text": titel, "warn": "⚠" in titel, "right": rechts}

    def panel_toggle_dictation(self):
        # Das Panel schliessen: waehrend des Diktats liegt der Fokus sonst hier
        # statt im Zielfenster, und der Text wuerde am falschen Ort landen.
        self._panel.close()
        self.toggle_dictation()

    def panel_open_settings(self):
        self.open_settings_menu()

    def panel_copy(self, text):
        self._panel.close()
        if write_clipboard(text.encode("utf-8")):
            notify("Diktat", "In Clipboard kopiert!")
        else:
            notify("Diktat", "Kopieren fehlgeschlagen")

    # ─── Einstellungen als Auswahl mit Haken ───

    def _update_settings_marks(self):
        """Setzt die Haken auf den geltenden Wert.

        Ein Haken beantwortet zwei Fragen auf einen Blick — was gilt, und was ein
        Klick tun wuerde. Die frueheren Titel („Erkennung: Whisper → auf Apple
        Speech") beantworteten die erste nur, indem man die zweite mitlas.
        """
        aktuelle_engine = asr.engine()
        for wert, item in self.engine_items.items():
            item.state = 1 if wert == aktuelle_engine else 0

        halten = push_to_talk()
        for wert, item in self.ptt_items.items():
            item.state = 1 if wert == halten else 0

        grenze = model_idle_timeout()
        for wert, item in self.idle_items.items():
            item.state = 1 if wert == grenze else 0

        self.llm_item.state = 1 if llm_enabled() else 0

    def _einstellung_setzen(self, schluessel, wert):
        self.settings[schluessel] = wert
        save_settings(self.settings)
        self._update_settings_marks()
        log(f"Einstellung {schluessel} = {wert!r}")

    def _pick_engine(self, sender):
        if sender._wert == asr.engine():
            return
        self._einstellung_setzen("asr_engine", sender._wert)
        notify("Erkennung", sender.title)
        self._refresh_panel_status()

    def _pick_ptt(self, sender):
        if sender._wert == push_to_talk():
            return
        self._einstellung_setzen("push_to_talk", sender._wert)
        notify("Hotkey", sender.title)

    def _pick_idle(self, sender):
        """Entlade-Zeitpunkt. Der Server liest die Einstellung bei jeder Pruefung
        neu, die Aenderung wirkt also ohne Neustart."""
        if sender._wert == model_idle_timeout():
            return
        self._einstellung_setzen("model_idle_timeout", sender._wert)
        notify("Modelle im Speicher", sender.title)

    def toggle_llm(self, _):
        """Schaltet die LLM-Bereinigung um.

        Aus bleibt nur der Verzoegerungslaut-Filter. War bisher eine Code-Konstante,
        also nur per Rebuild aenderbar.
        """
        neu = not llm_enabled()
        self._einstellung_setzen("llm_enabled", neu)
        notify("LLM-Bereinigung", "eingeschaltet" if neu else "ausgeschaltet")
        if neu:
            # Zaehler zuruecksetzen: das ausdrueckliche Einschalten ist die Bitte,
            # es noch einmal zu versuchen.
            self._llm_restart_versuche = 0
            self._llm_restart_zuletzt = 0.0
            self._start_llm_server()
        self._update_status()

    def open_settings_menu(self):
        """Klappt das Einstellungs-Untermenue am Zahnrad im Panel auf.

        Dasselbe NSMenu wie beim Rechtsklick — kein zweiter Aufbau, keine doppelte
        Pflege. Noetig wurde es, weil das verdichtete Panel den Hinweis
        „Rechtsklick auf das Icon" verloren hat: die Einstellungen lagen danach
        hinter einer Geste, die nichts mehr ankuendigt.
        """
        self._update_settings_marks()
        if self._panel is not None:
            self._panel.close()
        eintrag = self.menu.get("Einstellungen")
        untermenue = getattr(eintrag, "_menu", None)
        if untermenue is None:
            log("Einstellungs-Untermenue nicht gefunden — zeige das ganze Menue")
            self._show_menu()
            return
        # Am Statusitem-Button aufklappen, damit es dort erscheint, wo das Panel war.
        button = self._nsapp.nsstatusitem.button()
        untermenue.popUpMenuPositioningItem_atLocation_inView_(
            None, (0, button.bounds().size.height + 4), button
        )

    def unload_models(self, _):
        """Gibt den Speicher der Modelle frei — im Hintergrund.

        Nuetzlich vor anderen lokalen Modellen: beide zusammen belegen gemessen
        4,49 GB. Sie laden beim naechsten Diktat von selbst wieder (1,1 s bzw.
        1,5 s), es geht also nichts verloren.

        Der Aufruf wandert bewusst in einen Thread: der Server gibt erst frei, wenn
        eine laufende Generierung fertig ist, und wartet dabei auf dieselbe Sperre.
        Gemessen 5,2 s mitten in einer Bereinigung, bei einem langen Diktat
        entsprechend mehr — auf dem Main-Thread waere die Menueleiste so lange
        eingefroren.
        """
        notify("Modelle", "Speicher wird freigegeben…")
        threading.Thread(target=self._unload_models_worker, daemon=True).start()

    def _unload_models_worker(self):
        frei, fehler = asr.unload_models()
        if fehler:
            log(f"Entladen fehlgeschlagen: {fehler}")
            notify("Modelle", f"Entladen fehlgeschlagen: {str(fehler)[:80]}")
            return
        # Ein Server, der das Feld nicht kennt, liefert None — ohne diese Pruefung
        # scheitert die Formatierung mit TypeError und die Meldung bleibt aus.
        menge = f"{frei:.2f} GB" if isinstance(frei, (int, float)) else "Speicher"
        notify("Modelle", f"{menge} freigegeben")
        _on_main(self._update_status)

    def manage_glossary(self, _):
        """Fachbegriffe bearbeiten — einer pro Zeile.

        Bis hierher gab es dafuer gar keine Oberflaeche, obwohl die Liste der
        wirksamste Hebel im Projekt ist: sie hebt die Trefferquote bei Fachbegriffen
        von 7/10 auf 10/10, weil sie sowohl Whispers Dekodierung vorspannt als auch
        den phonetischen Abgleich speist.
        """
        begriffe = glossary.load_terms()
        text = _text_dialog(
            "Fachbegriffe",
            "Ein Begriff pro Zeile. Wirkt sofort, ohne Neustart.\n"
            "Begriffe unter 6 Zeichen werden übergangen — zu kurz für den "
            "phonetischen Abgleich.",
            "\n".join(begriffe),
        )
        if text is None:
            return

        neu = [z.strip() for z in text.splitlines() if z.strip()]
        try:
            with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
                json.dump(neu, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log(f"Glossar nicht schreibbar: {e}")
            notify("Fachbegriffe", "Konnte nicht gespeichert werden")
            return

        log(f"Glossar gespeichert: {len(neu)} Begriffe")
        notify("Fachbegriffe", f"{len(neu)} gespeichert")

    def _check_accessibility_on_launch(self):
        """Beim Start pruefen, ob am Cursor eingefuegt werden darf.

        Nach jedem Rebuild ist die Freigabe hin — die Ad-hoc-Signatur bindet sie an
        den cdhash des Binaries (siehe build.sh). Ohne diese Meldung merkt man das
        erst am ersten Diktat, das nur im Clipboard landet.

        Die Systemeinstellungen werden hier absichtlich *nicht* geoeffnet: die App
        startet auch per LaunchAgent bei jeder Anmeldung. Der Menueeintrag und
        `build.sh` uebernehmen das, wo es erwartet wird.
        """
        if permissions.is_trusted():
            log("Bedienungshilfen erteilt — Einfuegen am Cursor aktiv")
            return

        # Legt den TCC-Eintrag an, damit die App in der Liste steht.
        permissions.ensure_listed()
        log("Bedienungshilfen fehlen — Text landet nur im Clipboard")
        notify("Voice Transcript", "Bedienungshilfen fehlen — Menü öffnen zum Aktivieren")

    def fix_accessibility(self, _):
        if permissions.is_trusted():
            notify("Voice Transcript", "Bedienungshilfen sind erteilt")
            return

        permissions.ensure_listed()
        permissions.open_settings()
        notify(
            "Voice Transcript",
            "„Voice Transcript“ in der Liste aktivieren, dann App neu starten",
        )

    def _write_pid(self):
        with open(MENUBAR_PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        # atexit allein deckt nur den Start aus dem Quellbaum ab (Ctrl+C, sys.exit).
        # „Beenden“ landet in NSApp.terminate_, und AppKit beendet den Prozess mit
        # exit() aus C — Py_Finalize laeuft dabei nie, atexit-Handler feuern also
        # nicht. Gemessen: nach dem Beenden lief der LLM-Server mit 1,95 GB weiter
        # und die PID-Datei blieb mit einer toten PID liegen. Steht spaeter ein
        # fremder Prozess unter dieser PID, haelt _is_already_running() die App fuer
        # laufend und der naechste Start bricht wortlos ab.
        atexit.register(self._cleanup)
        self._terminate_observer = make_termination_observer(self._cleanup)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self._terminate_observer,
            "terminating:",
            NSApplicationWillTerminateNotification,
            None,
        )

    def _cleanup(self):
        """Server stoppen und PID-Datei entfernen.

        Darf mehrfach laufen: aus dem Quellbaum gestartet feuern Benachrichtigung
        *und* atexit.
        """
        if self._cleaned_up:
            return

        llm_stop_server()
        if self._llm_process is not None:
            self._llm_process.terminate()
        if os.path.exists(MENUBAR_PID_FILE):
            os.remove(MENUBAR_PID_FILE)

        # Erst hier, nicht am Anfang: bricht etwas oben ab, faengt der zweite Weg
        # (atexit) es auf. Stuende das Flag vorn, waere er stillgelegt und die
        # PID-Datei bliebe liegen — genau der Zustand, gegen den diese Methode
        # geschrieben ist, denn _is_already_running() haelt die App dann fuer
        # laufend und der naechste Start bricht wortlos ab.
        self._cleaned_up = True

    def _start_llm_server(self):
        """Startet den persistenten LLM-Server als separaten uv-Prozess."""
        if not llm_enabled() or llm_is_running():
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

    def _restart_llm_server_if_dead(self):
        """Startet den LLM-Server neu, wenn er weggestorben ist.

        Der Statustimer sah den Ausfall bisher (⚠ LLM nicht erreichbar) und tat
        nichts — ohne Neustart der ganzen App blieb die Bereinigung aus.

        Mit Rücknahme: kommt der Server prinzipiell nicht hoch, versuchte der
        5-Sekunden-Timer es gemessen 12x pro Minute, jedes Mal mit Benachrichtigung.
        """
        if not llm_enabled() or llm_is_running():
            self._llm_restart_versuche = 0   # laeuft wieder, Zaehler zuruecksetzen
            return

        # Ein noch laufender eigener Prozess heisst: das Modell laedt gerade.
        if self._llm_process is not None and self._llm_process.poll() is None:
            return

        if self._llm_restart_versuche >= LLM_RESTART_MAX:
            return  # aufgegeben; der Grund steht einmal im Log

        jetzt = time.monotonic()
        if jetzt - self._llm_restart_zuletzt < LLM_RESTART_INTERVAL:
            return

        self._llm_restart_zuletzt = jetzt
        self._llm_restart_versuche += 1
        log(f"LLM-Server ist weg — Neustart "
            f"(Versuch {self._llm_restart_versuche}/{LLM_RESTART_MAX})")
        self._start_llm_server()

        if self._llm_restart_versuche >= LLM_RESTART_MAX:
            log("LLM-Server startet nicht — keine weiteren Versuche. "
                "Im Menue LLM aus- und wieder einschalten, um es erneut zu probieren.")
            notify("LLM", "Server startet nicht — siehe app.log")

    def _register_hotkey(self):
        hk = self.settings["hotkey"]
        error = register_hotkey(hk["key"], hk["modifiers"],
                                self._hotkey_pressed, on_release=self._hotkey_released)
        if error:
            log(f"Hotkey-Registrierung fehlgeschlagen: {error}")
            notify("Hotkey", error)
        else:
            log(f"Hotkey registriert: {format_hotkey(hk['key'], hk['modifiers'])}")
        return error is None

    def _hotkey_pressed(self):
        """Hotkey gedrueckt. Bei Push-to-talk nur starten, sonst umschalten."""
        if not push_to_talk():
            self.toggle_dictation()
            return
        if not self.recording:
            self._start_dictation()

    def _hotkey_released(self):
        """Hotkey losgelassen — nur bei Push-to-talk von Bedeutung.

        Bewusst *nicht* auf _state == "recording" pruefen: bei einem kurzen Antippen
        kommt das Loslassen an, bevor der Diktat-Thread den Zustand gesetzt hat. Mit
        dieser Pruefung ging der Stopp verloren und die Aufnahme lief endlos weiter
        (gemessen bei 10 ms Tastendruck).

        stop_dictation() ist gefahrlos, wenn gerade nichts aufnimmt: es findet keine
        laufende Sitzung und keine lebende yap-PID und gibt False zurueck. Laeuft die
        Erkennung schon, ist der Aufruf ebenfalls ein Nulleffekt.
        """
        if not push_to_talk():
            return
        if self._dictation_thread is None or not self._dictation_thread.is_alive():
            return
        from voice_transcript.main import stop_dictation
        stop_dictation()

    def toggle_dictation(self, _=None):
        if self.recording:
            from voice_transcript.main import stop_dictation
            if stop_dictation():
                return

            # Kein laufender yap-Prozess, obwohl der Zustand "Aufnahme" oder
            # "Verarbeitung" meldet. Laeuft der Diktat-Thread noch, ist das die
            # LLM-Bereinigung — die darf nicht unterbrochen werden.
            if self._dictation_thread is not None and self._dictation_thread.is_alive():
                return

            # Thread ist weg, der Zustand aber nicht zurueckgesetzt: verwaist.
            # Ohne diese Freigabe blieb die recording-Property auf True und die
            # App war bis zum Neustart taub — der Hotkey lief in genau diesen
            # Zweig und kehrte wirkungslos zurueck.
            log(f"Verwaister Zustand '{self._state}' — zurueckgesetzt")
            self._set_state("idle")
            return

        self._start_dictation()

    def _start_dictation(self):
        self._dictation_thread = threading.Thread(target=self._run_dictation, daemon=True)
        self._dictation_thread.start()

    def _run_dictation(self):
        # Sofort sperren, damit ein schneller zweiter Hotkey-Druck nicht ein
        # zweites Diktat startet, bevor dictate() den Zustand meldet.
        self._set_state("recording")

        def on_start():
            self._set_state("recording")

        def on_stop():
            self._set_state("transcribing")

        def on_polish():
            self._set_state("processing")

        def on_result(text):
            _on_main(self._refresh_history)

        try:
            dictate(on_start=on_start, on_stop=on_stop, on_polish=on_polish,
                    on_result=on_result)
        except Exception as e:
            log(f"Diktat-Thread abgebrochen: {type(e).__name__}: {e}")
        finally:
            # Ein einziger Ort, der aufraeumt: verlaesst dictate() sich auf welchem
            # Weg auch immer, gibt der Zustand die App wieder frei. Vorher haetten
            # drei Zweige das leisten muessen — vergisst einer es, ist die App taub.
            self._set_state("idle")

    def _refresh_history(self):
        history_menu = self.menu["Letzte Diktate"]

        # clear() greift direkt auf das NSMenu zu, das erst mit dem ersten add()
        # entsteht. Beim App-Start ist es None — früher brach die Methode hier
        # komplett ab, weshalb das Untermenü bis zum ersten Diktat leer blieb.
        if history_menu._menu is not None:
            history_menu.clear()

        self._refresh_panel_history()

        history = load_history()
        if not history:
            history_menu.add(rumps.MenuItem("Noch keine Diktate"))
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
        if not text:
            return
        # Laeuft auf dem Main-Thread — ein haengendes pbcopy wuerde hier das ganze
        # Menue einfrieren, deshalb dieselbe Obergrenze wie im Diktat-Pfad.
        if write_clipboard(text.encode("utf-8")):
            notify("Diktat", "In Clipboard kopiert!")
        else:
            notify("Diktat", "Kopieren fehlgeschlagen")

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

            # Erst registrieren, dann speichern: eine belegte oder ungueltige
            # Kombination wuerde sonst in settings.json landen und die App beim
            # naechsten Start ohne funktionierenden Hotkey hochfahren.
            previous = self.settings["hotkey"]
            self.settings["hotkey"] = {"key": key, "modifiers": modifiers}
            if not self._register_hotkey():
                self.settings["hotkey"] = previous
                self._register_hotkey()
                return

            save_settings(self.settings)
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
        subprocess.run(["open", APP_SUPPORT_DIR], timeout=OPEN_TIMEOUT)

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
