"""
Cross-platform desktop notification helper.

Used by `memoria brief --delivery notify` to show a 2-line OS notification
when the morning brief is ready.

Platform support
────────────────
  Windows  — uses plyer (pip install plyer) if available; falls back to
             win10toast; falls back to Windows toast via PowerShell
  macOS    — uses osascript (no external dependency)
  Linux    — uses notify-send (standard on most desktops)

All failures are silently swallowed — notification delivery is best-effort.
"""

import subprocess
import sys
from typing import Optional


def send_notification(
    title: str,
    body: str,
    timeout: int = 10,
    app_name: str = "Memoria",
) -> bool:
    """
    Send a desktop notification. Returns True on success, False on failure.

    Parameters
    ──────────
    title   — notification headline (keep short — OS may truncate at ~64 chars)
    body    — notification body (max ~120 chars for best compatibility)
    timeout — seconds before the notification auto-dismisses (hint only)
    app_name — application label shown in the notification centre
    """
    try:
        return _send(title, body, timeout, app_name)
    except Exception:
        return False


# ─── Platform dispatchers ─────────────────────────────────────────────────────

def _send(title: str, body: str, timeout: int, app_name: str) -> bool:
    if sys.platform == "win32":
        return _send_windows(title, body, timeout, app_name)
    if sys.platform == "darwin":
        return _send_macos(title, body)
    return _send_linux(title, body, timeout, app_name)


def _send_windows(title: str, body: str, timeout: int, app_name: str) -> bool:
    # Tier 1: plyer (cross-platform, best UX on Windows 10+)
    try:
        from plyer import notification  # type: ignore
        notification.notify(
            title=title,
            message=body,
            app_name=app_name,
            timeout=timeout,
        )
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # Tier 2: win10toast
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(
            title, body,
            duration=timeout,
            threaded=True,
        )
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # Tier 3: PowerShell BurntToast / Windows.UI.Notifications fallback
    return _send_windows_ps(title, body)


def _send_windows_ps(title: str, body: str) -> bool:
    """Last-resort Windows notification via PowerShell."""
    ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$template.SelectSingleNode('//text[@id=1]').InnerText = '{_ps_escape(title)}'
$template.SelectSingleNode('//text[@id=2]').InnerText = '{_ps_escape(body)}'
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Memoria').Show($toast)
""".strip()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_macos(title: str, body: str) -> bool:
    """macOS notification via osascript (built-in, no deps)."""
    script = (
        f'display notification "{_applescript_escape(body)}" '
        f'with title "{_applescript_escape(title)}"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_linux(title: str, body: str, timeout: int, app_name: str) -> bool:
    """Linux notification via notify-send (libnotify, standard on most DEs)."""
    try:
        result = subprocess.run(
            [
                "notify-send",
                "--app-name", app_name,
                f"--expire-time={timeout * 1000}",
                title,
                body,
            ],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # notify-send not available — try plyer as fallback
        try:
            from plyer import notification  # type: ignore
            notification.notify(title=title, message=body, app_name=app_name, timeout=timeout)
            return True
        except Exception:
            return False
    except Exception:
        return False


# ─── String escaping helpers ──────────────────────────────────────────────────

def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _ps_escape(s: str) -> str:
    return s.replace("'", "''")
