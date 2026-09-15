"""Use IBus's session portal when the desktop provides it.

Qt 5's direct IBus discovery checks the daemon PID. That check fails when
the app and the desktop live in different PID namespaces, even though the
input-method socket is reachable. The supported portal avoids that check.
"""

import os
import subprocess


def prepare_input_method():
    """Configure this process before QApplication loads its input plugin."""
    if os.environ.get("QT_IM_MODULE") != "ibus" or "IBUS_USE_PORTAL" in os.environ:
        return
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
             "--object-path", "/org/freedesktop/DBus",
             "--method", "org.freedesktop.DBus.NameHasOwner", "org.freedesktop.portal.IBus"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return  # Keep the normal desktop configuration when a portal is absent.
    if result.returncode == 0 and result.stdout.strip() == "(true,)":
        os.environ["IBUS_USE_PORTAL"] = "1"
