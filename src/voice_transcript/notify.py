import subprocess


def notify(title, message):
    message = message.replace('"', '\\"')
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script])
