"""Where Paper Tablet keeps its own files.

Normal (installed or run from a checkout): settings and calibration in %APPDATA%\\PaperTablet,
the app's SSH key in %USERPROFILE%\\.ssh. Portable: when a file named `portable` (any content,
any extension such as portable.txt) sits in the app folder (the one holding `pc` and `tablet`),
everything stays in `data\\` next to it, so the folder can live on a USB stick and leaves
nothing behind on the PC.
"""
import os

PC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PC)


def portable():
    try:
        return any(n.lower().split(".")[0] == "portable" for n in os.listdir(ROOT)
                   if os.path.isfile(os.path.join(ROOT, n)))
    except OSError:
        return False


def data_dir():
    """Settings, calibration and (portable only) the SSH key and log."""
    if portable():
        d = os.path.join(ROOT, "data")
    else:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        d = os.path.join(base, "PaperTablet")
    os.makedirs(d, exist_ok=True)
    return d


def app_key():
    if portable():
        return os.path.join(data_dir(), "rmtablet_ed25519")
    return os.path.join(os.path.expanduser("~"), ".ssh", "rmtablet_ed25519")


def log_dir():
    """Portable: data\\. Installed (app folder not writable, or an installer marker present):
    %LOCALAPPDATA%\\PaperTablet. Run from a checkout: next to app.py, as before."""
    if portable():
        return data_dir()
    if os.path.exists(os.path.join(ROOT, "installed")) or not os.access(PC, os.W_OK):
        base = os.environ.get("LOCALAPPDATA") or data_dir()
        d = os.path.join(base, "PaperTablet")
        os.makedirs(d, exist_ok=True)
        return d
    return PC
