"""Popover-Panel am Menüleisten-Icon.

Ersetzt das NSMenu beim Linksklick durch ein kleines Fenster: Aufnahme-Taste,
Historie als Liste, eine Statuszeile. Der Rechtsklick zeigt weiter das gewohnte
Menü — der Code dafuer bleibt unberuehrt in menubar.py.

Reine Darstellung. Was angezeigt wird und was ein Klick bewirkt, liefert der
Delegate (VoiceTranscriptApp) ueber die panel_*-Methoden. Das haelt die
AppKit-Verdrahtung aus der Anwendungslogik heraus.

Layout-Entscheidungen und was sie gekostet haben:

- Das Hauptelement zeichnet sich selbst (siehe _Primary) und zeigt rechts, was im
  jeweiligen Zustand zaehlt: den Hotkey im Ruhezustand, die laufende Dauer waehrend
  der Aufnahme, die Engine waehrend der Verarbeitung.
- Keine Kopfzeile: wer das Panel oeffnet, hat gerade auf das Icon geklickt.
- Eine Statuszeile statt dreier. Die Bedienungshilfen-Warnung *ersetzt* den
  LLM-Status und faerbt den Punkt; die Engine steht rechts.
- Kein dauerhafter Rechtsklick-Hinweis mehr — eine Gebrauchsanweisung fuer etwas,
  das man einmal lernt.
- Die Historienzeilen heben sich unter dem Zeiger und sagen, was ein Klick tut.
  Dafuer braucht es eine eigene View mit NSTrackingArea: ein randloser NSButton
  hebt sich nicht von selbst hervor und meldet kein Betreten.

Zusammen 92 pt weniger Hoehe als die erste Fassung (392 -> 300).
"""
from AppKit import (
    NSApp,
    NSBezierPath,
    NSColor,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseUp,
    NSFont,
    NSFontWeightMedium,
    NSFontWeightSemibold,
    NSImage,
    NSLineBreakByTruncatingTail,
    NSMakeRect,
    NSMaxYEdge,
    NSNoBorder,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSProgressIndicator,
    NSProgressIndicatorStyleBar,
    NSScrollView,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSTextField,
    NSTrackingActiveInActiveApp,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
    NSViewController,
    NSButton,
)
import objc
from Foundation import NSObject, NSPointInRect

from voice_transcript.applog import log

WIDTH = 320
PAD = 16
INNER = WIDTH - 2 * PAD

BUTTON_Y = 12
BUTTON_H = 44
BUTTON_PAD = 12          # Innenabstand der Taste; daran haengt auch der Tabstopp
# Der Balken bekommt seinen Platz in *jedem* Zustand, auch wenn er nur waehrend
# Erkennung und Bereinigung sichtbar ist. Sonst springt das Panel in der Hoehe,
# waehrend man darauf wartet — genau im falschen Moment.
PROGRESS_Y = BUTTON_Y + BUTTON_H + 6
PROGRESS_H = 4
LIST_LABEL_Y = PROGRESS_Y + PROGRESS_H + 10
LIST_Y = LIST_LABEL_Y + 18
ROW_H = 30
LIST_H = 6 * ROW_H       # sechs Zeilen sichtbar, der Rest scrollt
STATUS_Y = LIST_Y + LIST_H + 14
STATUS_H = 20
HEIGHT = STATUS_Y + STATUS_H + 10

# Zeit + Abstand vor dem Text. Schmal, weil die Zeit in Tabellenziffern gesetzt ist
# und damit immer gleich breit baut — der Text beginnt so an derselben Kante.
ROW_TIME_W = 42
ROW_GAP = 8
ROW_INSET = 8
# Das Zahnrad sitzt neben der Diktier-Taste, nicht in einer eigenen Zeile — so
# kostet es keine Hoehe. Noetig wurde es, weil das verdichtete Panel den Hinweis
# „Rechtsklick auf das Icon" verloren hat: die Einstellungen lagen danach hinter
# einer Geste, die nichts mehr ankuendigt.
GEAR_W = 30
GEAR_GAP = 8
BUTTON_W = INNER - GEAR_W - GEAR_GAP

AKTION_TEXT = "Kopieren"
AKTION_W = 62
# Breite fuer die Engine rechts in der Statuszeile. 72 pt schnitten "Apple Speech"
# zu "Apple Spe…" ab — bei 11 pt Medium baut Text breiter als die Ueberschlagsrechnung.
ENGINE_W = 92

# Rot signalisiert die laufende Aufnahme — dieselbe Rolle wie beim Menueleisten-Icon.
TINTED_STATES = ("recording",)
# Waehrend dieser Zustaende arbeitet die App und das Hauptelement ist keine Taste
# mehr: der Klick wuerde nichts tun, also soll es auch nicht wie eine aussehen.
WORKING_STATES = ("transcribing", "processing")


class _Flipped(NSView):
    """View mit Ursprung oben links.

    AppKit rechnet von unten links. Bei einem Panel, das man von oben nach unten
    liest, fuehrt das zu Koordinaten, die man dauernd im Kopf umdrehen muss.
    """

    def isFlipped(self):
        return True


def _label(text, y, size, weight, color, x=PAD, width=INNER,
           align=NSTextAlignmentLeft, mono=False):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(NSMakeRect(x, y, width, size + 6))
    if mono:
        # Tabellenziffern: jede Ziffer gleich breit, damit die Spalte steht.
        field.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight))
    else:
        field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(color)
    field.setAlignment_(align)
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return field


# ─────────────────────────── Hauptelement ───────────────────────────

class _Primary(NSView):
    """Diktier-Taste und Zustandsanzeige in einem, selbst gezeichnet.

    Eine eigene View, weil eine bezelte NSButton sich nicht faerben laesst:
    setBezelColor_ *und* setContentTintColor_ bleiben dort wirkungslos, gemessen 0
    von 12.672 Pixeln. Nur der attributierte Titel zeichnet — und ein weisser Titel
    auf grauem Bezel waere unlesbar. Also den Hintergrund selbst malen.

    Drei Erscheinungen, eine Flaeche:
      Ruhe        Akzentfarbe gefuellt, Hotkey rechts — sieht wie die Hauptaktion aus
      Aufnahme    Systemrot gefuellt, laufende Sekunden rechts
      Arbeit      ruhig gefuellt, Phase links, Engine rechts; kein Klickziel
    """

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _event):
        return True

    def hitTest_(self, punkt):
        """Die Klickflaeche ist die ganze View, nicht ihre Labels.

        Ohne diese Methode liefert hitTest_ das NSTextField obendrauf (gemessen), und
        weder mouseDown_ noch mouseUp_ kommen hier an — die Zeile war damit gar nicht
        anklickbar. Der Punkt kommt in Koordinaten des Superviews.
        """
        if NSPointInRect(punkt, self.frame()):
            return self
        return None

    def mouseDown_(self, _event):
        # Muss da sein, auch wenn sie nichts tut: NSResponder.mouseDown_ gibt das
        # Ereignis standardmaessig an die naechste Instanz weiter, und dann kommt
        # auch das zugehoerige mouseUp_ nicht mehr hier an. Gemessen: ohne diese
        # Methode loeste ein echter Klick 0x aus. Meine frueheren Tests riefen
        # mouseUp_ direkt auf und haben das vollstaendig verdeckt.
        pass

    def drawRect_(self, _rect):
        if self.state in TINTED_STATES:
            NSColor.systemRedColor().set()
        elif self.state in WORKING_STATES:
            NSColor.unemphasizedSelectedContentBackgroundColor().set()
        else:
            NSColor.controlAccentColor().set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 7.0, 7.0
        ).fill()

    def mouseUp_(self, _event):
        # Waehrend der Verarbeitung ist es kein Knopf — ein Klick soll nicht den
        # Eindruck erwecken, er tue etwas.
        if self.state in WORKING_STATES:
            return
        if self.on_click is not None:
            self.on_click()

    @objc.python_method
    def apply(self, state, titel, detail):
        self.state = state
        gefuellt = state not in WORKING_STATES
        vordergrund = (NSColor.alternateSelectedControlTextColor() if gefuellt
                       else NSColor.labelColor())

        self.titel_label.setStringValue_(titel)
        self.titel_label.setTextColor_(vordergrund)
        self.detail_label.setStringValue_(detail or "")
        self.detail_label.setTextColor_(vordergrund.colorWithAlphaComponent_(0.78))
        self.setNeedsDisplay_(True)


def make_primary(on_click):
    view = _Primary.alloc().initWithFrame_(
        NSMakeRect(PAD, BUTTON_Y, BUTTON_W, BUTTON_H)
    )
    view.state = "idle"
    view.on_click = on_click

    hoehe = 20
    y = (BUTTON_H - hoehe) / 2
    view.titel_label = _label(
        "", y, 14, NSFontWeightMedium, NSColor.labelColor(),
        x=BUTTON_PAD, width=BUTTON_W - 2 * BUTTON_PAD - 62,
    )
    view.detail_label = _label(
        "", y + 2, 11, NSFontWeightMedium, NSColor.labelColor(),
        x=BUTTON_W - BUTTON_PAD - 62, width=62, align=NSTextAlignmentRight, mono=True,
    )
    view.addSubview_(view.titel_label)
    view.addSubview_(view.detail_label)
    return view


# ─────────────────────────── Historienzeile ───────────────────────────

class _Row(NSView):
    """Eine Historienzeile, die auf den Zeiger reagiert.

    Ein randloser NSButton kann das nicht: er hebt sich nicht hervor und meldet kein
    Betreten. Deshalb eine eigene View mit NSTrackingArea — der Preis fuer das, was
    am Panel am meisten fehlte, naemlich ein Hinweis darauf, dass eine Zeile
    anklickbar ist.
    """

    def isFlipped(self):
        return True

    def isOpaque(self):
        return False

    def acceptsFirstMouse_(self, _event):
        # Im Popover soll der erste Klick schon kopieren und nicht bloss aktivieren.
        return True

    def hitTest_(self, punkt):
        """Wie bei _Primary: die ganze Zeile ist die Klickflaeche, nicht ihre Labels."""
        if NSPointInRect(punkt, self.frame()):
            return self
        return None

    def mouseDown_(self, _event):
        # Siehe _Primary.mouseDown_: ohne sie beansprucht die View das Ereignis
        # nicht und mouseUp_ kommt nie an.
        pass

    def updateTrackingAreas(self):
        # Bei jeder Groessenaenderung neu setzen, sonst zeigt der alte Bereich auf
        # eine Flaeche, die es nicht mehr gibt.
        for bereich in self.trackingAreas():
            self.removeTrackingArea_(bereich)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited
                | NSTrackingActiveInActiveApp
                | NSTrackingInVisibleRect,
                self,
                None,
            )
        )

    def drawRect_(self, _rect):
        if not self.hover:
            return
        NSColor.selectedContentBackgroundColor().set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 5.0, 5.0
        ).fill()

    def mouseEntered_(self, _event):
        self.hover = True
        self._farben_setzen()
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        self.hover = False
        self._farben_setzen()
        self.setNeedsDisplay_(True)

    def mouseUp_(self, _event):
        if self.on_click is not None:
            self.on_click(self.index)

    @objc.python_method
    def _farben_setzen(self):
        """Farben *und* Textbreite umschalten.

        Die Breite muss mit: waere der Platz fuer „Kopieren" dauerhaft reserviert,
        kostete er jede Zeile rund 20 Zeichen — gemessen blieben von 46 nur 25
        uebrig. Der Hinweis erscheint nur unter dem Zeiger, also darf er auch nur
        dann Platz nehmen.
        """
        rahmen = self.text_label.frame()
        if self.hover:
            weiss = NSColor.alternateSelectedControlTextColor()
            self.zeit_label.setTextColor_(weiss.colorWithAlphaComponent_(0.72))
            self.text_label.setTextColor_(weiss)
            self.aktion_label.setTextColor_(weiss.colorWithAlphaComponent_(0.85))
            self.aktion_label.setHidden_(False)
            self.text_label.setFrame_(NSMakeRect(
                rahmen.origin.x, rahmen.origin.y, self.text_w_hover, rahmen.size.height))
        else:
            self.zeit_label.setTextColor_(NSColor.tertiaryLabelColor())
            self.text_label.setTextColor_(NSColor.labelColor())
            self.aktion_label.setHidden_(True)
            self.text_label.setFrame_(NSMakeRect(
                rahmen.origin.x, rahmen.origin.y, self.text_w_voll, rahmen.size.height))


def make_row(index, eintrag, on_click):
    """Baut eine Historienzeile.

    Modulfunktion, weil PyObjC jede Methode einer ObjC-Subklasse als Selektor deutet
    und eine Fabrik mit Argumenten zu keinem passt.
    """
    row = _Row.alloc().initWithFrame_(NSMakeRect(0, index * ROW_H, INNER, ROW_H - 1))
    row.hover = False
    row.index = index
    row.on_click = on_click

    row.zeit_label = _label(
        eintrag["time"], 7, 11, NSFontWeightMedium, NSColor.tertiaryLabelColor(),
        x=ROW_INSET, width=ROW_TIME_W, mono=True,
    )
    text_x = ROW_INSET + ROW_TIME_W + ROW_GAP
    # Volle Breite im Ruhezustand; nur unter dem Zeiger macht der Text Platz.
    row.text_w_voll = INNER - text_x - ROW_INSET
    row.text_w_hover = row.text_w_voll - AKTION_W
    row.text_label = _label(
        eintrag["label"], 6, 12, NSFontWeightMedium, NSColor.labelColor(),
        x=text_x, width=row.text_w_voll,
    )
    row.aktion_label = _label(
        AKTION_TEXT, 7, 11, NSFontWeightMedium, NSColor.labelColor(),
        x=INNER - ROW_INSET - AKTION_W, width=AKTION_W, align=NSTextAlignmentRight,
    )
    row.aktion_label.setHidden_(True)

    for teil in (row.zeit_label, row.text_label, row.aktion_label):
        row.addSubview_(teil)

    # Den Tracking-Bereich hier selbst setzen und nicht darauf hoffen, dass AppKit
    # updateTrackingAreas rechtzeitig ruft: gemessen hatte die Zeile auch im Fenster
    # noch null Bereiche — mouseEntered waere nie gefeuert und der Hover, also der
    # ganze Sinn der eigenen Row-View, blieb aus. Spaetere Aufrufe von AppKit
    # ersetzen den Bereich, sie stapeln ihn nicht (geprueft).
    row.updateTrackingAreas()
    return row


# ─────────────────────────── Panel-Inhalt ───────────────────────────

class PanelController(NSViewController):
    """Inhalt des Popovers. Instanzieren ueber make_controller()."""

    # ─── Aufbau ───

    @objc.python_method
    def _build(self):
        root = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))

        self.primary = make_primary(self._primaer_geklickt)
        root.addSubview_(self.primary)

        # Unbestimmt, und das ist die ehrliche Form: weder Whisper noch das LLM
        # liefern eine Quote. Der Balken sagt "es laeuft", nicht "so weit".
        self.progress = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(PAD, PROGRESS_Y, BUTTON_W, PROGRESS_H)
        )
        self.progress.setStyle_(NSProgressIndicatorStyleBar)
        self.progress.setIndeterminate_(True)
        self.progress.setHidden_(True)
        root.addSubview_(self.progress)

        self.gear_button = NSButton.buttonWithTitle_target_action_(
            "", self, "gearClicked:"
        )
        self.gear_button.setFrame_(NSMakeRect(
            PAD + BUTTON_W + GEAR_GAP, BUTTON_Y, GEAR_W, BUTTON_H))
        self.gear_button.setBordered_(False)
        symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "gearshape", "Einstellungen"
        )
        if symbol is not None:
            self.gear_button.setImage_(symbol)
        else:
            # Aeltere Systeme kennen das SF-Symbol nicht — dann eben als Zeichen.
            self.gear_button.setTitle_("⚙")
        self.gear_button.setToolTip_("Einstellungen")
        root.addSubview_(self.gear_button)

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

        # Statuszeile: Punkt, Text, rechts die Engine. Der Text ist anklickbar, weil
        # hier auch die Bedienungshilfen-Warnung erscheint.
        self.status_dot = _label(
            "●", STATUS_Y + 3, 9, NSFontWeightSemibold, NSColor.tertiaryLabelColor(),
            x=PAD, width=12,
        )
        root.addSubview_(self.status_dot)

        self.status_button = NSButton.buttonWithTitle_target_action_(
            "", self, "statusClicked:"
        )
        self.status_button.setFrame_(
            NSMakeRect(PAD + 14, STATUS_Y, INNER - 14 - ENGINE_W, STATUS_H)
        )
        self.status_button.setBordered_(False)
        self.status_button.setAlignment_(NSTextAlignmentLeft)
        self.status_button.setFont_(
            NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium)
        )
        root.addSubview_(self.status_button)

        self.engine_label = _label(
            "", STATUS_Y + 1, 11, NSFontWeightMedium, NSColor.tertiaryLabelColor(),
            x=WIDTH - PAD - ENGINE_W, width=ENGINE_W, align=NSTextAlignmentRight,
        )
        root.addSubview_(self.engine_label)

        self.setView_(root)

    # ─── Aktualisieren ───

    @objc.python_method
    def refresh(self):
        """Alles neu — beim Oeffnen des Panels."""
        self.refresh_status()
        self.refresh_history()

    @objc.python_method
    def refresh_status(self):
        """Taste und Statuszeile. Billig, darf im Sekundentakt laufen.

        Bewusst *ohne* die Historie: die wurde hier frueher mitgezogen, wodurch der
        5-Sekunden-Timer alle Zeilen loeschte und neu erzeugte. Die Scroll-Position
        sprang dabei zurueck nach oben, und noetig war es nie — die Historie aendert
        sich nur nach einem Diktat.
        """
        state = self.delegate.panel_state()
        self._status = self.delegate.panel_status()

        # Rechts steht, was in diesem Zustand zaehlt: der Hotkey, solange man ihn
        # druecken kann, sonst die laufende Dauer. Die Engine gehoert nicht hierher —
        # sie steht schon in der Statuszeile, und doppelt gesagt ist nichts gewonnen.
        if state in TINTED_STATES or state in WORKING_STATES:
            detail = self.delegate.panel_elapsed() or ""
        else:
            detail = self.delegate.panel_hotkey_label()

        self.primary.apply(state, self.delegate.panel_dictate_label(), detail)

        arbeitet = state in WORKING_STATES
        self.progress.setHidden_(not arbeitet)
        if arbeitet:
            self.progress.startAnimation_(None)
        else:
            self.progress.stopAnimation_(None)
        warnung = bool(self._status.get("warn"))
        self.status_dot.setTextColor_(
            NSColor.systemOrangeColor() if warnung else NSColor.systemGreenColor()
        )
        self.status_button.setTitle_(self._status.get("text", ""))
        self.status_button.setContentTintColor_(
            NSColor.systemOrangeColor() if warnung else NSColor.secondaryLabelColor()
        )
        self.status_button.setEnabled_(bool(self._status.get("action")))
        self.engine_label.setStringValue_(self._status.get("right", ""))

    @objc.python_method
    def refresh_history(self):
        """Liste neu aufbauen. Nur wenn sich die Historie geaendert hat — der
        Neuaufbau setzt die Scroll-Position zurueck."""
        for zeile in self._row_views:
            zeile.removeFromSuperview()
        self._row_views = []

        self._history = self.delegate.panel_history()

        if not self._history:
            self.rows.setFrameSize_((INNER, LIST_H))
            leer = _label(
                "Noch keine Diktate", 6, 11, NSFontWeightMedium,
                NSColor.tertiaryLabelColor(), x=ROW_INSET, width=INNER - 2 * ROW_INSET,
            )
            self.rows.addSubview_(leer)
            self._row_views.append(leer)
            return

        # Dokument-View mitwachsen lassen, sonst scrollt die Liste nicht.
        self.rows.setFrameSize_((INNER, max(len(self._history) * ROW_H, LIST_H)))
        for index, eintrag in enumerate(self._history):
            zeile = make_row(index, eintrag, self._history_geklickt)
            self.rows.addSubview_(zeile)
            self._row_views.append(zeile)

    # ─── Aktionen ───

    @objc.python_method
    def _history_geklickt(self, index):
        if 0 <= index < len(self._history):
            self.delegate.panel_copy(self._history[index]["text"])

    @objc.python_method
    def _primaer_geklickt(self):
        self.delegate.panel_toggle_dictation()

    def gearClicked_(self, _sender):
        self.delegate.panel_open_settings()

    def statusClicked_(self, _sender):
        aktion = (self._status or {}).get("action")
        if aktion:
            self.delegate.panel_status_action(aktion)


def make_controller(delegate):
    """Baut den Panel-Inhalt.

    Als Modulfunktion statt Klassenmethode: PyObjC deutet jede Methode einer
    ObjC-Subklasse als Selektor, und `create(cls, delegate)` passt zu keinem — das
    schlaegt bereits beim Import fehl (BadPrototypeError).

    initWithNibName_bundle_(None, None) statt init(): sonst sucht der Controller
    nach einem Nib, das es nicht gibt. Die View entsteht gleich hier statt in einem
    spaeten loadView() — das hat im Test eine ContentSize von 0x0 geliefert.
    """
    controller = PanelController.alloc().initWithNibName_bundle_(None, None)
    controller.delegate = delegate
    controller._history = []
    controller._row_views = []
    controller._status = {}
    controller._build()
    return controller


# ─────────────────────────── Statusitem-Anbindung ───────────────────────────

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


def make_router(on_click):
    router = ClickRouter.alloc().init()
    router.on_click = on_click
    return router


def attach(status_item, on_click):
    """Klemmt das Menue ab und laesst Klicks stattdessen an on_click gehen.

    Solange ein Menue am Statusitem haengt, oeffnet der Linksklick immer dieses
    Menue und die Action der Taste feuert nie — es muss also weg. Der Rechtsklick
    haengt es kurz zurueck (siehe menubar._show_menu).

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
        # Den unbestimmten Balken anhalten: er zeichnet sonst unsichtbar weiter,
        # solange die Verarbeitung laeuft.
        try:
            self.controller.progress.stopAnimation_(None)
        except Exception as e:
            log(f"Balken nicht anhaltbar: {type(e).__name__}: {e}")
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
        """Taste und Statuszeile nachziehen — billig, laeuft im Sekundentakt."""
        self._if_open("Status", self.controller.refresh_status)

    def refresh_history_if_open(self):
        """Liste neu aufbauen. Nur aufrufen, wenn sich die Historie geaendert hat:
        der Neuaufbau setzt die Scroll-Position zurueck."""
        self._if_open("Historie", self.controller.refresh_history)
