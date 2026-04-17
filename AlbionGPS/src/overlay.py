"""
Overlay in-game Albion GPS - carte de navigation reglable style Waze.

Fenetre PyQt6 frameless, transparente, always-on-top, "tool" (hors taskbar),
qui suit la fenetre d'Albion et dessine une carte de navigation compacte
par-dessus le jeu :
- Gros disque couleur a gauche avec fleche directionnelle (direction
  cardinale vers la prochaine zone).
- Titre "PROCHAINE ZONE" + nom de la zone en grand, colore selon le danger
  (bleu / jaune / rouge / noir).
- Ligne secondaire avec nombre de sauts restants + zone de depart actuelle.
- Pastille de danger a droite (SURE / JAUNE / ROUGE / NOIR).

L'overlay est reglable via OverlayConfig :
- opacity : 0.15 a 1.0 (transparence globale, pour ne pas masquer les ennemis)
- scale   : 0.5 a 1.5 (taille de la carte)
- anchor  : coin de reference (TL/TR/BL/BR)
- offset_x, offset_y : ecart en pixels depuis le coin choisi

L'overlay ne capte pas les clics (WindowTransparentForInput), ne perturbe
donc pas le jeu. Il lit uniquement la position/taille de la fenetre
d'Albion via les APIs Windows standard.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import QWidget

from .game_detector import GameDetector, GameWindow


# ============================================================================
# Data
# ============================================================================

@dataclass
class OverlayState:
    next_zone: Optional[str] = None
    next_zone_color: str = "blue"
    current_zone: Optional[str] = None
    remaining_hops: int = 0
    direction: Optional[str] = None


@dataclass
class OverlayConfig:
    """Configuration visuelle persistante de l'overlay."""
    opacity: float = 0.55          # 0.15 - 1.0
    scale: float = 0.80            # 0.50 - 1.50
    anchor: str = "TR"             # TL, TR, BL, BR
    offset_x: int = 24
    offset_y: int = 24

    def clamp(self) -> "OverlayConfig":
        self.opacity = max(0.15, min(1.0, self.opacity))
        self.scale = max(0.5, min(1.5, self.scale))
        if self.anchor not in ("TL", "TR", "BL", "BR"):
            self.anchor = "TR"
        return self

    @classmethod
    def load(cls, path: Path) -> "OverlayConfig":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            return cfg.clamp()
        except Exception:
            return cls()

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError:
            pass


# Palette alignee sur main.py (Waze-style).
ZONE_COLOR = {
    "blue":   QColor("#4FA3FF"),
    "yellow": QColor("#F4C430"),
    "red":    QColor("#EF4444"),
    "black":  QColor("#B070FF"),
}
ZONE_LABEL = {
    "blue":   "SURE",
    "yellow": "JAUNE",
    "red":    "ROUGE",
    "black":  "NOIR",
}

# Direction cardinale : code court (utilise par pathfinding) -> libelle FR
# long pour l'affichage principal + libelle court pour la mini-pastille.
DIRECTION_FR = {
    "N":  ("Nord",       "N"),
    "S":  ("Sud",        "S"),
    "E":  ("Est",        "E"),
    "W":  ("Ouest",      "O"),
    "NE": ("Nord-Est",   "NE"),
    "NW": ("Nord-Ouest", "NO"),
    "SE": ("Sud-Est",    "SE"),
    "SW": ("Sud-Ouest",  "SO"),
}

BG_CARD    = QColor(11, 19, 32, 210)
BG_INNER   = QColor(22, 32, 51, 190)
BORDER     = QColor(51, 204, 255, 160)
BORDER_DIM = QColor(37, 51, 73, 140)
TEXT       = QColor("#F1F5F9")
TEXT_DIM   = QColor("#8B9BB0")
SHADOW     = QColor(0, 0, 0, 100)


# Taille "1x" de la carte (scale=1.0). Valeurs sensiblement reduites par
# rapport a la v1 pour etre moins envahissantes.
BASE_CARD_W = 300
BASE_CARD_H = 108


class GPSOverlay(QWidget):
    """Fenetre transparente always-on-top qui suit Albion."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self.state = OverlayState()
        self.detector = GameDetector()
        self._last_game_rect: Optional[QRect] = None

        self._config_path = config_path
        if config_path is not None and config_path.exists():
            self.config = OverlayConfig.load(config_path)
        else:
            self.config = OverlayConfig()
        self.setWindowOpacity(self.config.opacity)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> bool:
        if not self._align_to_game():
            return False
        self.setWindowOpacity(self.config.opacity)
        self.show()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def update_state(self, new_state: OverlayState) -> None:
        self.state = new_state
        self.update()

    def update_config(self, **kwargs) -> None:
        """Met a jour la config live et sauvegarde."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        self.config.clamp()
        self.setWindowOpacity(self.config.opacity)
        self.update()
        if self._config_path is not None:
            self.config.save(self._config_path)

    # ------------------------------------------------------------- tick

    def _tick(self) -> None:
        if not self._align_to_game():
            self.hide()
        else:
            if not self.isVisible():
                self.show()

    def _align_to_game(self) -> bool:
        win: Optional[GameWindow] = self.detector.get_game_window()
        if win is None:
            return False
        r = win.rect
        new_rect = QRect(r.left, r.top, r.width, r.height)
        if new_rect != self._last_game_rect:
            self._last_game_rect = new_rect
            self.setGeometry(new_rect)
        return True

    # ------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:  # noqa: D401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        scale = self.config.scale
        card_w = int(BASE_CARD_W * scale)
        card_h = int(BASE_CARD_H * scale)

        # -- Largeur responsive : si le nom de la prochaine zone ou la ligne
        # "X sauts - depuis Y" depassent la largeur de base, on elargit la
        # carte pour que tout tienne sans tronquer. Max = 90% de l'ecran du
        # jeu pour eviter les cas pathologiques.
        if self.state.next_zone is not None:
            needed = self._compute_needed_width(painter, scale)
            if needed > card_w:
                card_w = min(needed, int(w * 0.9))

        # Position selon l'ancre + offset
        ax = self.config.anchor[1] if len(self.config.anchor) == 2 else "R"
        ay = self.config.anchor[0] if len(self.config.anchor) == 2 else "T"
        ox = self.config.offset_x
        oy = self.config.offset_y
        x = ox if ax == "L" else w - card_w - ox
        y = oy if ay == "T" else h - card_h - oy
        # Clamp dans l'ecran de jeu (evite de masquer la carte off-screen)
        x = max(0, min(w - card_w, x))
        y = max(0, min(h - card_h, y))

        self._draw_card(painter, x, y, card_w, card_h, scale)

        if self.state.next_zone is None:
            self._draw_idle(painter, x, y, card_w, card_h, scale)
            painter.end()
            return

        self._draw_navigation(painter, x, y, card_w, card_h, scale)
        painter.end()

    # ------------------------------------------------------------ helpers

    def _f(self, base: float, scale: float) -> int:
        """Arrondit une dimension scaled."""
        return max(1, int(round(base * scale)))

    def _compute_needed_width(self, p: QPainter, scale: float) -> int:
        """Calcule la largeur de carte necessaire pour afficher entierement
        le nom de la prochaine zone et la ligne 'X sauts - depuis Y' sans
        troncature. On mesure chaque texte avec la police reellement utilisee
        au rendu, puis on ajoute : disque + paddings + pastille danger."""
        disc_d = self._f(74, scale)
        left_pad = self._f(16, scale)           # marge gauche avant disque
        gap = self._f(14, scale)                # espace disque <-> texte
        right_pad = self._f(16, scale)          # marge droite
        pill_w = self._f(50, scale) + self._f(8, scale)  # pastille + gap

        # Mesure nom zone
        p.setFont(self._font(14, QFont.Weight.Black, scale))
        name_w = p.fontMetrics().horizontalAdvance(self.state.next_zone or "")

        # Mesure ligne sauts + direction + depuis
        hops = self.state.remaining_hops
        hops_text = f"{hops} saut" + ("s" if hops > 1 else "")
        sub_parts = [hops_text]
        dir_long, _ = DIRECTION_FR.get(
            (self.state.direction or "").upper(), ("", "")
        )
        if dir_long:
            sub_parts.append(dir_long)
        if self.state.current_zone:
            sub_parts.append(f"depuis {self.state.current_zone}")
        sub = "  ·  ".join(sub_parts)
        p.setFont(self._font(9, QFont.Weight.DemiBold, scale))
        sub_w = p.fontMetrics().horizontalAdvance(sub)

        # Mesure hint
        p.setFont(self._font(7, QFont.Weight.Normal, scale))
        hint_w = p.fontMetrics().horizontalAdvance("Prends le portail nomme ci-dessus.")

        text_w = max(name_w + pill_w, sub_w, hint_w)
        return left_pad + disc_d + gap + text_w + right_pad

    def _font(self, size_pt: float, weight: QFont.Weight, scale: float) -> QFont:
        return QFont("Segoe UI", max(6, int(round(size_pt * scale))), weight)

    def _draw_card(self, p: QPainter, x: int, y: int, w: int, h: int, scale: float) -> None:
        radius = self._f(16, scale)

        # Ombre portee legere
        shadow_rect = QRectF(x + 3, y + 4, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(SHADOW)
        p.drawRoundedRect(shadow_rect, radius, radius)

        # Fond principal
        main_rect = QRectF(x, y, w, h)
        p.setBrush(BG_CARD)
        p.setPen(QPen(BORDER_DIM, 1))
        p.drawRoundedRect(main_rect, radius, radius)

        # Lisere accent en haut
        accent_rect = QRectF(x + self._f(16, scale), y, w - self._f(32, scale), self._f(2, scale))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(BORDER))
        p.drawRoundedRect(accent_rect, 1.2, 1.2)

    def _draw_idle(self, p: QPainter, x: int, y: int, w: int, h: int, scale: float) -> None:
        pad = self._f(18, scale)
        p.setPen(TEXT_DIM)
        p.setFont(self._font(8, QFont.Weight.Bold, scale))
        p.drawText(
            QRectF(x + pad, y + self._f(14, scale), w - 2 * pad, self._f(14, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "ALBION GPS",
        )
        p.setPen(TEXT)
        p.setFont(self._font(12, QFont.Weight.Bold, scale))
        p.drawText(
            QRectF(x + pad, y + self._f(34, scale), w - 2 * pad, self._f(26, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "En attente",
        )
        p.setPen(TEXT_DIM)
        p.setFont(self._font(8, QFont.Weight.Normal, scale))
        p.drawText(
            QRectF(x + pad, y + self._f(62, scale), w - 2 * pad, self._f(32, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            "Lance un trajet dans l'app.",
        )

    def _draw_navigation(self, p: QPainter, x: int, y: int, w: int, h: int, scale: float) -> None:
        zone_color = ZONE_COLOR.get(self.state.next_zone_color, QColor("#4FA3FF"))
        danger_label = ZONE_LABEL.get(self.state.next_zone_color, "")

        # Disque directionnel
        disc_d = self._f(74, scale)
        disc_x = x + self._f(16, scale)
        disc_y = y + (h - disc_d) // 2
        disc_rect = QRectF(disc_x, disc_y, disc_d, disc_d)

        halo = QColor(zone_color)
        halo.setAlpha(40)
        p.setBrush(halo)
        p.setPen(Qt.PenStyle.NoPen)
        ext = self._f(5, scale)
        p.drawEllipse(QRectF(disc_x - ext, disc_y - ext, disc_d + 2 * ext, disc_d + 2 * ext))

        p.setBrush(BG_INNER)
        p.setPen(QPen(zone_color, max(2.0, 2.5 * scale)))
        p.drawEllipse(disc_rect)

        self._draw_arrow(
            p,
            cx=disc_x + disc_d // 2,
            cy=disc_y + disc_d // 2 - self._f(4, scale),
            size=self._f(40, scale),
            direction=self.state.direction,
            color=zone_color,
        )

        # Libelle direction court (NE / SO / N...) sous la fleche, inscrit
        # dans le disque. Indispensable pour l'orientation in-game Albion.
        _, dir_short_label = DIRECTION_FR.get(
            (self.state.direction or "").upper(), ("", "")
        )
        if dir_short_label:
            p.setPen(zone_color)
            p.setFont(self._font(8, QFont.Weight.Black, scale))
            label_rect = QRectF(
                disc_x,
                disc_y + disc_d - self._f(22, scale),
                disc_d,
                self._f(14, scale),
            )
            p.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, dir_short_label)

        # Texte
        text_x = disc_x + disc_d + self._f(14, scale)
        text_w = (x + w) - text_x - self._f(16, scale)

        p.setPen(TEXT_DIM)
        p.setFont(self._font(7, QFont.Weight.Bold, scale))
        p.drawText(
            QRectF(text_x, y + self._f(12, scale), text_w, self._f(12, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "PROCHAINE ZONE",
        )

        # Nom zone
        p.setPen(zone_color)
        p.setFont(self._font(14, QFont.Weight.Black, scale))
        name_rect = QRectF(text_x, y + self._f(24, scale), text_w, self._f(26, scale))
        pill_w = self._f(50, scale)
        name = self._elide(p, self.state.next_zone or "", text_w - pill_w - self._f(8, scale))
        p.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )

        # Pastille danger
        if danger_label:
            self._draw_danger_pill(
                p,
                x + w - self._f(16, scale) - pill_w,
                y + self._f(16, scale),
                pill_w,
                self._f(18, scale),
                zone_color,
                danger_label,
                scale,
            )

        # Ligne sauts + direction + depuis
        hops = self.state.remaining_hops
        hops_text = f"{hops} saut" + ("s" if hops > 1 else "")
        sub_parts = [hops_text]
        dir_long, dir_short = DIRECTION_FR.get(
            (self.state.direction or "").upper(), ("", "")
        )
        if dir_long:
            sub_parts.append(dir_long)
        if self.state.current_zone:
            sub_parts.append(f"depuis {self.state.current_zone}")
        sub = "  ·  ".join(sub_parts)

        p.setPen(TEXT)
        p.setFont(self._font(9, QFont.Weight.DemiBold, scale))
        p.drawText(
            QRectF(text_x, y + h - self._f(28, scale), text_w, self._f(16, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._elide(p, sub, text_w),
        )

        p.setPen(TEXT_DIM)
        p.setFont(self._font(7, QFont.Weight.Normal, scale))
        p.drawText(
            QRectF(text_x, y + h - self._f(14, scale), text_w, self._f(12, scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._elide(p, "Prends le portail nomme ci-dessus.", text_w),
        )

    # -------------------------------------------------------- primitives

    @staticmethod
    def _elide(p: QPainter, text: str, max_px: int) -> str:
        fm = p.fontMetrics()
        if fm.horizontalAdvance(text) <= max_px:
            return text
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, max_px)

    @staticmethod
    def _draw_danger_pill(
        p: QPainter,
        x: int,
        y: int,
        w: int,
        h: int,
        color: QColor,
        label: str,
        scale: float,
    ) -> None:
        rect = QRectF(x, y, w, h)
        bg = QColor(color)
        bg.setAlpha(40)
        p.setBrush(bg)
        p.setPen(QPen(color, 1))
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(color)
        p.setFont(QFont("Segoe UI", max(6, int(round(7 * scale))), QFont.Weight.Black))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _draw_arrow(
        p: QPainter,
        cx: int,
        cy: int,
        size: int,
        direction: Optional[str],
        color: QColor,
    ) -> None:
        angles = {
            "N":  -90, "S":  90,
            "E":  0,   "W":  180,
            "NE": -45, "NW": -135,
            "SE": 45,  "SW": 135,
        }
        deg = angles.get((direction or "N").upper(), -90)

        p.save()
        p.translate(cx, cy)
        p.rotate(deg)

        s = size / 2
        head_w = size * 0.55
        head_h = size * 0.72
        shaft_h = size * 0.28
        shaft_w = s * 1.6

        poly = QPolygonF(
            [
                QPointF(-shaft_w, -shaft_h / 2),
                QPointF(s - head_w, -shaft_h / 2),
                QPointF(s - head_w, -head_h / 2),
                QPointF(s + shaft_h, 0),
                QPointF(s - head_w, head_h / 2),
                QPointF(s - head_w, shaft_h / 2),
                QPointF(-shaft_w, shaft_h / 2),
            ]
        )
        p.setBrush(color)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1.2))
        p.drawPolygon(poly)
        p.restore()
