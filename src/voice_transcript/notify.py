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


def notify(title, message):
    subprocess.run(["osascript", "-e", _SCRIPT, str(message), str(title)])
