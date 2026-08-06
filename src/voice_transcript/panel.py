"""Popover-Panel am Menüleisten-Icon.

Ersetzt das NSMenu beim Linksklick durch ein kleines Fenster: grosse
Aufnahme-Taste, Historie als Liste, Statuszeile. Der Rechtsklick zeigt weiter das
gewohnte Menü — der Code dafuer bleibt unberuehrt in menubar.py.

Reine Darstellung. Was angezeigt wird und was ein Klick bewirkt, liefert der
Delegate (VoiceTranscriptApp) ueber die panel_*-Methoden. Das haelt die
AppKit-Verdrahtung aus der Anwendungslogik heraus.
"""
from AppKit import (
    NSApp,
    NSBezelStyleRounded,
    NSColor,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseUp,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSLineBreakByTruncatingTail,
    NSMutableAttributedString,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSMakeRect,
    NSNoBorder,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextField,
    NSView,
    NSViewController,
    NSButton,
    NSFontWeightMedium,
    NSFontWeightSemibold,
    NSMaxYEdge,
)
import objc
from Foundation import NSObject

from voice_transcript.applog import log

WIDTH = 320
PAD = 16
INNER = WIDTH - 2 * PAD

HEADER_Y = 14
BUTTON_Y = 40
BUTTON_H = 44
HOTKEY_Y = BUTTON_Y + BUTTON_H + 6
LIST_LABEL_Y = HOTKEY_Y + 26
LIST_Y = LIST_LABEL_Y + 20
LIST_H = 176
ROW_H = 30
STATUS_Y = LIST_Y + LIST_H + 14
STATUS_ROW_H = 20
HINT_Y = STATUS_Y + 2 * STATUS_ROW_H + 4
HEIGHT = HINT_Y + 22

# Rot signalisiert die laufende Aufnahme — dieselbe Rolle wie beim Menueleisten-Icon.
TINTED_STATES = ("recording",)


class _Flipped(NSView):
    """View mit Ursprung oben links.

    AppKit rechnet von unten links. Bei einem Panel, das man von oben nach unten
    liest, fuehrt das zu Koordinaten, die man dauernd im Kopf umdrehen muss.
    """

    def isFlipped(self):
        return True


def _label(text, y, size, weight, color, x=PAD, width=INNER,
           align=NSTextAlignmentLeft):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(NSMakeRect(x, y, width, size + 6))
    field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(color)
    field.setAlignment_(align)
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return field


def _titel(text, farbe):
    """Attributierter Tastentitel — die einzige Faerbung, die bei einer bezelten
    Taste wirklich zeichnet.

    setBezelColor_ und setContentTintColor_ bleiben dort wirkungslos: gemessen 0 von
    12.672 Pixeln rot. Der Aufnahme-Zustand war dadurch nicht zu erkennen, obwohl
    bezelColor gesetzt *war* — ein Test, der nur die Eigenschaft abfragt, haette das
    nie gefunden.
    """
    stil = NSMutableParagraphStyle.alloc().init()
    stil.setAlignment_(NSTextAlignmentCenter)
    return NSMutableAttributedString.alloc().initWithString_attributes_(
        text,
        {
            NSForegroundColorAttributeName: farbe,
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(14, NSFontWeightMedium),
            NSParagraphStyleAttributeName: stil,
        },
    )


class PanelController(NSViewController):
    """Inhalt des Popovers. Instanzieren ueber make_controller()."""

    # ─── Aufbau ───

    @objc.python_method
    def _build(self):
        root = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))

        root.addSubview_(_label(
            "Voice Transcript", HEADER_Y, 12, NSFontWeightSemibold,
            NSColor.secondaryLabelColor(),
        ))

        self.dictate_button = NSButton.buttonWithTitle_target_action_(
            "Diktieren", self, "dictateClicked:"
        )
        self.dictate_button.setFrame_(NSMakeRect(PAD, BUTTON_Y, INNER, BUTTON_H))
        self.dictate_button.setBezelStyle_(NSBezelStyleRounded)
        self.dictate_button.setFont_(
            NSFont.systemFontOfSize_weight_(14, NSFontWeightMedium)
        )
        root.addSubview_(self.dictate_button)

        self.hotkey_label = _label(
            "", HOTKEY_Y, 11, NSFontWeightMedium, NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        )
        root.addSubview_(self.hotkey_label)

        root.addSubview_(_label(
            "Letzte Diktate", LIST_LABEL_Y, 11, NSFontWeightMedium,
            NSColor.secondaryLabelColor(),
        ))

        self.scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(PAD, LIST_Y, INNER, LIST_H)
        )
        self.scroll.setBorderType_(NSNoBorder)
        self.scroll.setDrawsBackground_(False)
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setAutohidesScrollers_(True)
        self.rows = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, INNER, LIST_H))
        self.scroll.setDocumentView_(self.rows)
        root.addSubview_(self.scroll)

        # Zwei Statuszeilen reichen: LLM und — nur wenn noetig — Bedienungshilfen.
        for index in range(2):
            button = NSButton.buttonWithTitle_target_action_(
                "", self, "statusClicked:"
            )
            button.setFrame_(
                NSMakeRect(PAD, STATUS_Y + index * STATUS_ROW_H, INNER, STATUS_ROW_H)
            )
            button.setBordered_(False)
            button.setAlignment_(NSTextAlignmentLeft)
            button.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            button.setTag_(index)
            button.setHidden_(True)
            root.addSubview_(button)
            self._status_buttons.append(button)

        # 11pt sekundaer statt 10pt tertiaer: der Hinweis ist der einzige Weg, das
        # Rechtsklick-Menue zu entdecken — dafuer war er die kleinste und blasseste
        # Schrift im Panel.
        root.addSubview_(_label(
            "Rechtsklick auf das Icon: Einstellungen", HINT_Y, 11,
            NSFontWeightMedium, NSColor.secondaryLabelColor(),
        ))

        self.setView_(root)

    # ─── Aktualisieren ───

    @objc.python_method
    def refresh(self):
        """Alles neu — beim Oeffnen des Panels."""
        self.refresh_status()
        self.refresh_history()

    @objc.python_method
    def refresh_status(self):
        """Taste und Statuszeilen. Billig, darf im Sekundentakt laufen.

        Bewusst *ohne* die Historie: die wurde hier frueher mitgezogen, wodurch der
        5-Sekunden-Timer alle Zeilen loeschte und neu erzeugte. Die Scroll-Position
        sprang dabei zurueck nach oben, und noetig war es nie — die Historie aendert
        sich nur nach einem Diktat.
        """
        state = self.delegate.panel_state()
        self.hotkey_label.setStringValue_(self.delegate.panel_hotkey_label())

        farbe = (NSColor.systemRedColor() if state in TINTED_STATES
                 else NSColor.labelColor())
        self.dictate_button.setAttributedTitle_(
            _titel(self.delegate.panel_dictate_label(), farbe)
        )
        self._refresh_status()

    @objc.python_method
    def refresh_history(self):
        """Liste neu aufbauen. Nur wenn sich die Historie wirklich geaendert hat."""
        for button in self._row_buttons:
            button.removeFromSuperview()
        self._row_buttons = []

        self._history = self.delegate.panel_history()

        if not self._history:
            self.rows.setFrameSize_((INNER, LIST_H))
            empty = _label(
                "Noch keine Diktate", 6, 11, NSFontWeightMedium,
                NSColor.tertiaryLabelColor(), x=2, width=INNER - 4,
            )
            self.rows.addSubview_(empty)
            self._row_buttons.append(empty)
            return

        # Dokument-View mitwachsen lassen, sonst scrollt die Liste nicht.
        height = max(len(self._history) * ROW_H, LIST_H)
        self.rows.setFrameSize_((INNER, height))

        for index, entry in enumerate(self._history):
            button = NSButton.buttonWithTitle_target_action_(
                f"{entry['time']}   {entry['label']}", self, "historyClicked:"
            )
            button.setFrame_(NSMakeRect(0, index * ROW_H, INNER, ROW_H - 2))
            button.setBordered_(False)
            button.setAlignment_(NSTextAlignmentLeft)
            button.setFont_(NSFont.systemFontOfSize_weight_(12, NSFontWeightMedium))
            # Reicht die Breite nicht, kuerzt die Taste am Ende statt hart zu
            # beschneiden — der Delegate kuerzt schon vor, das hier ist die Reserve.
            button.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
            # Der Index verbindet Taste und Eintrag — an eine NSButton-Instanz
            # laesst sich kein Python-Attribut haengen.
            button.setTag_(index)
            self.rows.addSubview_(button)
            self._row_buttons.append(button)

    @objc.python_method
    def _refresh_status(self):
        lines = self.delegate.panel_status_lines()
        self._status_lines = lines
        for index, button in enumerate(self._status_buttons):
            if index >= len(lines):
                button.setHidden_(True)
                continue
            line = lines[index]
            button.setTitle_(line["text"])
            button.setEnabled_(bool(line.get("action")))
            button.setContentTintColor_(
                NSColor.systemOrangeColor() if line.get("warn")
                else NSColor.secondaryLabelColor()
            )
            button.setHidden_(False)

    # ─── Aktionen (ObjC-Selektoren) ───

    def dictateClicked_(self, _sender):
        self.delegate.panel_toggle_dictation()

    def historyClicked_(self, sender):
        index = sender.tag()
        if 0 <= index < len(self._history):
            self.delegate.panel_copy(self._history[index]["text"])

    def statusClicked_(self, sender):
        # Nach dem Namen der Aktion greifen, nicht nach der Zeilennummer: die
        # Zeilen entstehen aus dem Zustand und koennen zwischen Zeichnen und Klick
        # die Reihenfolge wechseln.
        index = sender.tag()
        if 0 <= index < len(self._status_lines):
            action = self._status_lines[index].get("action")
            if action:
                self.delegate.panel_status_action(action)


class ClickRouter(NSObject):
    """Leitet Klicks auf das Statusitem an Python weiter.

    Braucht es, weil target/action ein ObjC-Objekt verlangen — rumps.App ist eine
    reine Python-Klasse und kann das nicht sein.
    """

    def clicked_(self, _sender):
        event = NSApp.currentEvent()
        right = event is not None and (
            event.type() == NSEventTypeRightMouseUp
            or bool(event.modifierFlags() & NSEventModifierFlagControl)
        )
        self.on_click(right)


def make_controller(delegate):
    """Baut den Panel-Inhalt.

    Als Modulfunktion statt Klassenmethode: PyObjC deutet jede Methode einer
    ObjC-Subklasse als Selektor, und `create(cls, delegate)` passt zu keinem —
    das schlaegt bereits beim Import fehl (BadPrototypeError).

    initWithNibName_bundle_(None, None) statt init(): sonst sucht der Controller
    nach einem Nib, das es nicht gibt. Die View entsteht gleich hier statt in einem
    spaeten loadView() — das hat im Test eine ContentSize von 0x0 geliefert.
    """
    controller = PanelController.alloc().initWithNibName_bundle_(None, None)
    controller.delegate = delegate
    controller._history = []
    controller._row_buttons = []
    controller._status_buttons = []
    controller._status_lines = []
    controller._build()
    return controller


def make_router(on_click):
    router = ClickRouter.alloc().init()
    router.on_click = on_click
    return router


def attach(status_item, on_click):
    """Klemmt das Menue ab und laesst Klicks stattdessen an on_click gehen.

    Solange ein Menue am Statusitem haengt, oeffnet der Linksklick immer dieses
    Menue und die Action der Taste feuert nie — es muss also weg. Der Rechtsklick
    haengt es kurz zurueck (siehe menubar.show_menu).

    Rueckgabe: (button, router). Der Router muss am Leben bleiben, sonst raeumt der
    GC das Action-Target weg.
    """
    button = status_item.button()
    if button is None:
        raise RuntimeError("Statusitem hat keine button() — Popover nicht moeglich")

    router = make_router(on_click)
    status_item.setMenu_(None)
    button.setTarget_(router)
    button.setAction_("clicked:")
    button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
    return button, router


class Panel:
    """Haelt Popover und Controller und regelt das Auf und Zu."""

    def __init__(self, delegate, button):
        self.button = button
        self.controller = make_controller(delegate)
        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(self.controller)
        self.popover.setContentSize_((WIDTH, HEIGHT))
        # Transient: ein Klick daneben schliesst das Panel, wie beim Menue.
        self.popover.setBehavior_(NSPopoverBehaviorTransient)

    @property
    def is_open(self):
        return bool(self.popover.isShown())

    def show(self):
        """Oeffnet das Panel. Rueckgabe: True, wenn es tatsaechlich sichtbar ist.

        Die Rueckmeldung ist wichtig, damit der Aufrufer auf das Menue ausweichen
        kann. Ein Popover laesst sich nicht immer zeigen — bei schlafendem Display
        etwa bleibt isShown() False, ohne dass ein Fehler geworfen wird. Ohne
        Rueckfall waere der Linksklick dann einfach wirkungslos.
        """
        try:
            self.controller.refresh()
            # Ohne aktive App bekommt das Popover keinen Fokus und schliesst sich
            # teils sofort wieder — als LSUIElement muss die App das anstossen.
            NSApp.activateIgnoringOtherApps_(True)
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                self.button.bounds(), self.button, NSMaxYEdge
            )
        except Exception as e:
            log(f"Panel liess sich nicht oeffnen: {type(e).__name__}: {e}")
            return False
        return self.is_open

    def close(self):
        self.popover.performClose_(None)

    def toggle(self):
        """Rueckgabe: True, wenn danach etwas sichtbar ist bzw. korrekt geschlossen."""
        if self.is_open:
            self.close()
            return True
        return self.show()

    def _if_open(self, what, fn):
        if not self.is_open:
            return
        try:
            fn()
        except Exception as e:
            log(f"Panel-Aktualisierung ({what}) fehlgeschlagen: {type(e).__name__}: {e}")

    def refresh_status_if_open(self):
        """Taste und Statuszeilen nachziehen — billig, laeuft im Sekundentakt."""
        self._if_open("Status", self.controller.refresh_status)

    def refresh_history_if_open(self):
        """Liste neu aufbauen. Nur aufrufen, wenn sich die Historie geaendert hat:
        der Neuaufbau setzt die Scroll-Position zurueck."""
        self._if_open("Historie", self.controller.refresh_history)
