"""
Detection de la fenetre du jeu Albion Online.

Fournit une classe GameDetector qui sait dire si le jeu tourne et, si oui,
ou se trouve sa fenetre a l'ecran (position + taille). L'overlay s'en sert
pour se positionner et se redimensionner automatiquement par-dessus Albion.

Aucune lecture de la memoire du jeu : on utilise uniquement les APIs
Windows standard (enumeration des fenetres, enumeration des processus).
Compatible Tools of Service d'Albion Online.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

# Ces imports sont Windows-only. On protege pour eviter les crashes sur
# Linux/Mac au cas ou quelqu'un teste le moteur ailleurs.
_WINDOWS = sys.platform.startswith("win")

if _WINDOWS:
    import ctypes
    from ctypes import wintypes

    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore
else:
    psutil = None  # type: ignore


# Noms de processus et titres de fenetres connus pour Albion Online.
# On matche de maniere insensible a la casse.
ALBION_PROCESS_NAMES = ("albion-online.exe", "albiononline.exe", "albion.exe")
ALBION_WINDOW_TITLE_HINTS = ("albion online", "albion")


@dataclass
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class GameWindow:
    hwnd: int
    pid: int
    title: str
    rect: WindowRect


class GameDetector:
    """Detecte si Albion tourne et ou se trouve sa fenetre."""

    def __init__(self) -> None:
        if not _WINDOWS:
            self._user32 = None
            return
        self._user32 = ctypes.windll.user32

    # -------------------------------------------------------- process-based

    def find_albion_pids(self) -> list[int]:
        """Retourne les PIDs des processus Albion trouves, ou [] si aucun."""
        if psutil is None:
            return []
        pids: list[int] = []
        for proc in psutil.process_iter(attrs=["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:
                continue
            if name in ALBION_PROCESS_NAMES:
                pids.append(proc.info["pid"])
        return pids

    def is_game_running(self) -> bool:
        return bool(self.find_albion_pids()) or self.find_window_by_title() is not None

    # -------------------------------------------------------- window-based

    def find_window_by_title(self) -> Optional[GameWindow]:
        """Enumere les fenetres de premier plan et renvoie la premiere qui
        ressemble a Albion Online. Pratique si psutil n'est pas dispo."""
        if not _WINDOWS or self._user32 is None:
            return None

        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
        GetWindowText = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

        found: list[GameWindow] = []

        def _callback(hwnd: int, _lparam: int) -> bool:
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLength(hwnd)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            title = buff.value
            title_low = title.lower().strip()
            if not title_low:
                return True
            if not any(h in title_low for h in ALBION_WINDOW_TITLE_HINTS):
                return True
            # Titre match : on recupere le rect et le pid.
            rect = self._get_rect(hwnd)
            if rect is None:
                return True
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append(GameWindow(hwnd=hwnd, pid=pid.value, title=title, rect=rect))
            return False  # stop enumeration : on a trouve

        EnumWindows(EnumWindowsProc(_callback), 0)
        return found[0] if found else None

    def _get_rect(self, hwnd: int) -> Optional[WindowRect]:
        if not _WINDOWS:
            return None
        rect = wintypes.RECT()
        ok = ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if not ok:
            return None
        return WindowRect(rect.left, rect.top, rect.right, rect.bottom)

    def get_game_window(self) -> Optional[GameWindow]:
        return self.find_window_by_title()


# ---------------------------------------------------------- smoke test CLI

def _main() -> None:
    det = GameDetector()
    print(f"Platform Windows : {_WINDOWS}")
    print(f"psutil dispo     : {psutil is not None}")
    pids = det.find_albion_pids()
    print(f"PIDs Albion      : {pids or 'aucun'}")
    w = det.get_game_window()
    if w is None:
        print("Fenetre Albion   : non trouvee")
    else:
        r = w.rect
        print(f"Fenetre Albion   : '{w.title}' pid={w.pid}")
        print(f"  Position       : ({r.left},{r.top}) -> ({r.right},{r.bottom})")
        print(f"  Taille         : {r.width}x{r.height}")


if __name__ == "__main__":
    _main()
