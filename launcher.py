"""Start / stop helper used by the Windows scripts (Linux has its own .sh scripts). Standard library only.

  python launcher.py start      start the app if it is not running, then open it in the browser
  python launcher.py stop       ask a running app to close (same as the Quit button)
  python launcher.py lan        start in the foreground for phones/tablets on the home network (prints address + PIN)
  python launcher.py shortcuts  create Desktop and Start-menu shortcuts (Windows)
  python launcher.py unshortcuts  remove them
"""
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = os.environ.get("STORYTELLER_PORT", "8000")   # (only changed for testing)
URL = f"http://127.0.0.1:{PORT}"
WIN = os.name == "nt"
PY = HERE / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")
PYW = HERE / ".venv" / "Scripts" / "pythonw.exe"


def up() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/stories", timeout=1.5):
            return True
    except Exception:
        return False


def start() -> int:
    if not PY.exists():
        print("First-time setup is not done. Double-click install.bat first (see README.md).")
        return 1
    if not up():
        (HERE / "data").mkdir(exist_ok=True)
        log = open(HERE / "data" / "server.log", "ab")
        cmd = [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", PORT]
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW) if WIN else 0
        kw = {"creationflags": flags} if WIN else {"start_new_session": True}
        subprocess.Popen(cmd, cwd=str(HERE), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        for _ in range(80):
            if up():
                break
            time.sleep(0.5)
        if not up():
            print("It did not start. Details are in data\\server.log")
            return 1
    webbrowser.open(URL)
    return 0


def stop() -> int:
    if not up():
        print("AI Storyteller is not running.")
        return 0
    try:
        req = urllib.request.Request(URL + "/api/quit", method="POST", data=b"{}", headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        print("AI Storyteller is closing.")
    except Exception as e:
        print("Could not close it:", e)
        return 1
    return 0


def lan() -> int:
    if not PY.exists():
        print("First-time setup is not done. Run install.bat first.")
        return 1
    (HERE / "data").mkdir(exist_ok=True)
    pin_file = HERE / "data" / "lan_pin"
    if not pin_file.exists() or not pin_file.read_text().strip():
        pin_file.write_text("".join(secrets.choice("0123456789") for _ in range(6)))
    pin = pin_file.read_text().strip()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = socket.gethostbyname(socket.gethostname())
    print(f"\n  On your phone or tablet open:  http://{ip}:8000\n  PIN: {pin}   (stored in data\\lan_pin; delete that file to get a new one)\n")
    print("  If Windows asks to allow the app through the firewall, allow it on PRIVATE networks only.\n")
    env = {**os.environ, "STORYTELLER_PIN": pin}
    return subprocess.call([str(PY), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"], cwd=str(HERE), env=env)


def _shortcut_paths() -> list[Path]:
    desk = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [desk / "AI Storyteller.lnk", menu / "AI Storyteller.lnk"]


def shortcuts() -> int:
    if not WIN:
        print("Shortcuts are only made on Windows.")
        return 0
    ico = HERE / "static" / "icon.ico"
    try:
        from PIL import Image
        Image.open(HERE / "static" / "icon.png").convert("RGBA").save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        ico = None
    target = PYW if PYW.exists() else PY
    for lnk in _shortcut_paths():
        if not lnk.parent.exists():
            continue
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.Arguments='\"%s\" start';"
              "$s.WorkingDirectory='%s';%s$s.Description='AI Storyteller';$s.Save()") % (
            lnk, target, HERE / "launcher.py", HERE, ("$s.IconLocation='%s';" % ico) if ico else "")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    print("Added 'AI Storyteller' to your Desktop and Start menu.")
    return 0


def unshortcuts() -> int:
    for lnk in _shortcut_paths():
        lnk.unlink(missing_ok=True)
    (HERE / "static" / "icon.ico").unlink(missing_ok=True)
    print("Removed the AI Storyteller shortcuts. Your data folder was left alone.")
    return 0


if __name__ == "__main__":
    cmds = {"start": start, "stop": stop, "lan": lan, "shortcuts": shortcuts, "unshortcuts": unshortcuts}
    sys.exit(cmds.get(sys.argv[1] if len(sys.argv) > 1 else "start", start)())
