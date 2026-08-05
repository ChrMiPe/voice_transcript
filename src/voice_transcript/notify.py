import subprocess

# Text als Argument statt in den Script-Body: seit Fehlermeldungen von yap in
# Benachrichtigungen landen, kann der Inhalt Anfuehrungszeichen, Backslashes
# oder Zeilenumbrueche enthalten. Die frueher benutzte Escape-Regel (nur " ->
# \") haette daran das ganze AppleScript zerlegt.
_SCRIPT = """\
on run argv
    display notification (item 1 of argv) with title (item 2 of argv)
end run\
"""


# notify() wird aus dem Diktat-Thread aufgerufen. Ohne Obergrenze haelt ein
# klemmender Benachrichtigungs-Dienst das ganze Diktat auf — eine Meldung ist das
# nicht wert.
NOTIFY_TIMEOUT = 10


def notify(title, message):
    try:
        subprocess.run(
            ["osascript", "-e", _SCRIPT, str(message), str(title)],
            capture_output=True,
            timeout=NOTIFY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # Eine verschluckte Benachrichtigung darf nichts abbrechen.
