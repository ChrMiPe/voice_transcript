"""Mikrofonaufnahme in eine WAV-Datei.

Braucht die Whisper-Engine: `yap` nimmt selbst auf, Whisper will ein fertiges
Audio. AVAudioRecorder schreibt direkt in eine Datei — deutlich weniger Code als
ein AVAudioEngine-Tap, und keine Python-Aufrufe aus dem Audio-Thread.

Laeuft auch ohne NSApp-Run-Loop (geprueft), der CLI-Pfad ueber
`python -m voice_transcript` funktioniert also genauso wie die Menueleisten-App.

Bewusst ohne numpy: dieses Modul laeuft *in* der App, und das Bundle soll klein
bleiben. Die Samples liest der LLM-Server aus der Datei — dort liegt Whisper.
"""
import os
import tempfile

from voice_transcript.applog import log
from voice_transcript.config import MIN_AUDIO_BYTES

# Whisper erwartet 16 kHz Mono — direkt so aufnehmen erspart das Umrechnen.
SAMPLE_RATE = 16000
_FORMAT_LINEAR_PCM = 1819304813  # 'lpcm' als FourCC


def _settings():
    from AVFoundation import (
        AVFormatIDKey,
        AVLinearPCMBitDepthKey,
        AVLinearPCMIsFloatKey,
        AVNumberOfChannelsKey,
        AVSampleRateKey,
    )

    return {
        AVFormatIDKey: _FORMAT_LINEAR_PCM,
        AVSampleRateKey: float(SAMPLE_RATE),
        AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: False,
    }


class Recorder:
    """Eine Aufnahme in eine temporaere WAV-Datei."""

    def __init__(self):
        handle, self.path = tempfile.mkstemp(prefix="voice_transcript_", suffix=".wav")
        os.close(handle)
        os.remove(self.path)  # AVAudioRecorder legt die Datei selbst an
        self._recorder = None

    def start(self):
        """Startet die Aufnahme. Rueckgabe: None bei Erfolg, sonst Fehlermeldung."""
        try:
            from AVFoundation import AVAudioRecorder
            from Foundation import NSURL
        except ImportError as e:
            return f"AVFoundation fehlt: {e}"

        url = NSURL.fileURLWithPath_(self.path)
        recorder, error = AVAudioRecorder.alloc().initWithURL_settings_error_(
            url, _settings(), None
        )
        if recorder is None:
            return f"Recorder nicht anlegbar: {error}"
        if not recorder.prepareToRecord():
            return "Recorder nicht vorbereitbar (Mikrofon belegt?)"
        if not recorder.record():
            # Ohne Mikrofon-Berechtigung schlaegt genau das fehl.
            return "Aufnahme nicht startbar — Mikrofon-Zugriff pruefen"

        self._recorder = recorder
        return None

    @property
    def is_recording(self):
        return self._recorder is not None and bool(self._recorder.isRecording())

    def stop(self):
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

    def has_audio(self):
        """True, wenn die Aufnahme lang genug ist, um transkribiert zu werden.

        Nicht nur "Datei vorhanden": eine sofort gestoppte Aufnahme ergibt eine
        WAV-Datei mit Kopfzeile und ein paar Millisekunden Stille (gemessen 4096
        Bytes). Die haette Whisper *und* danach noch yap beschaeftigt, um am Ende
        „Kein Text erkannt" zu melden.
        """
        try:
            return os.path.getsize(self.path) >= MIN_AUDIO_BYTES
        except OSError:
            return False

    def cleanup(self):
        try:
            os.remove(self.path)
        except OSError as e:
            log(f"Aufnahme nicht loeschbar: {e}")
