"""
Albion GPS - Application principale (PyQt6).

Refonte graphique "Waze-style" :
- Theme sombre navy + accent cyan, cartes arrondies, typographie claire.
- Planificateur de trajet en haut (depart / arrivee / swap / CTA).
- Resume de trajet en bande colore (sauts, safety, ETA).
- Timeline verticale des zones a traverser (pastilles colorees + icones).
- Footer "navigation" avec zone courante, autodetect et bouton overlay.
- Panneau diagnostic du sniffer repliable pour ne pas polluer l'UI.

Lancer : python -m src.main  (depuis le dossier AlbionGPS/)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QIntValidator
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .game_detector import GameDetector
from .overlay import GPSOverlay, OverlayState
from .pathfinding import Route, WorldGraph, compute_cardinal
from .photon_sniffer import PhotonSniffer
from .resources import (
    KIND_BY_KEY,
    ResourceKind,
    categories,
    enchants_for,
    format_item,
    kinds_in_category,
    tiers_for,
)
from .shopping_list import ShoppingEntry, ShoppingList
from .world_index import WorldIndex


DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "zones.json"
WORLD_XML = Path(__file__).resolve().parent.parent / "data" / "world.xml"
DUMP_PATH = Path(__file__).resolve().parent.parent / "data" / "photon_capture.log"
OVERLAY_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "overlay_config.json"
SHOPPING_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "shopping_list.json"
MARKET_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "market_discovery.log"


# ============================================================================
# Palette Waze-style
# ============================================================================
# Fond navy profond + accent cyan Waze + couleurs Albion preservees pour les
# pastilles de danger. Tout le theme est centralise ici pour pouvoir ajuster
# en un seul endroit.
BG          = "#0B1320"  # fond principal
BG_CARD     = "#162033"  # cartes / panneaux
BG_CARD_HI  = "#1E2B43"  # hover / survol
BORDER      = "#253349"  # lisere des cartes
ACCENT      = "#33CCFF"  # cyan Waze
ACCENT_HOT  = "#5BD8FF"
TEXT        = "#F1F5F9"
TEXT_DIM    = "#8B9BB0"
TEXT_MUTED  = "#64748B"
SUCCESS     = "#4ADE80"
WARNING     = "#F4C430"
DANGER      = "#EF4444"
PURPLE      = "#B070FF"

# Couleurs Albion par type de zone
ZONE_COLOR = {
    "blue":   "#4FA3FF",
    "yellow": "#F4C430",
    "red":    "#EF4444",
    "black":  "#B070FF",
}
ZONE_LABEL = {
    "blue":   "SURE",
    "yellow": "JAUNE",
    "red":    "ROUGE",
    "black":  "NOIR",
}


# Feuille de style globale. Applee sur QApplication pour couvrir la totalite
# des widgets. On garde les selecteurs larges (QPushButton, QComboBox...) et
# on ajoute des objectName pour les cas specifiques (#primaryButton, etc.).
GLOBAL_QSS = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
}}

QMainWindow, #centralContainer {{
    background-color: {BG};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* --- Cartes --- */
QFrame#card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#softCard {{
    background-color: {BG_CARD};
    border-radius: 12px;
}}
QFrame#routeStripSafe {{
    background-color: {BG_CARD};
    border: 1px solid {SUCCESS};
    border-radius: 12px;
}}
QFrame#routeStripWarn {{
    background-color: {BG_CARD};
    border: 1px solid {WARNING};
    border-radius: 12px;
}}
QFrame#routeStripDanger {{
    background-color: {BG_CARD};
    border: 1px solid {DANGER};
    border-radius: 12px;
}}

/* --- Labels --- */
QLabel#appTitle {{
    font-size: 14pt;
    font-weight: 800;
    color: {TEXT};
    letter-spacing: 1px;
}}
QLabel#appSubtitle {{
    font-size: 7pt;
    color: {TEXT_DIM};
    letter-spacing: 2px;
}}
QLabel#sectionLabel {{
    font-size: 8pt;
    color: {TEXT_DIM};
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#cardTitle {{
    font-size: 11pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#summaryMain {{
    font-size: 12pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#summarySub {{
    font-size: 8pt;
    color: {TEXT_DIM};
}}
QLabel#currentZoneName {{
    font-size: 12pt;
    font-weight: 800;
    color: {ACCENT};
}}
QLabel#currentZoneLabel {{
    font-size: 8pt;
    color: {TEXT_DIM};
    letter-spacing: 1px;
    font-weight: 700;
}}
QLabel#statusChip {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 6px 12px;
    color: {TEXT_DIM};
    font-size: 9pt;
}}
QLabel#statusChipOk {{
    background-color: rgba(74, 222, 128, 0.12);
    border: 1px solid {SUCCESS};
    border-radius: 12px;
    padding: 6px 12px;
    color: {SUCCESS};
    font-size: 9pt;
    font-weight: 700;
}}
QLabel#statusChipOff {{
    background-color: rgba(239, 68, 68, 0.10);
    border: 1px solid {DANGER};
    border-radius: 12px;
    padding: 6px 12px;
    color: {DANGER};
    font-size: 9pt;
    font-weight: 700;
}}

/* --- Combobox editable (barre de recherche transparente) --- */
QComboBox {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {BORDER};
    border-radius: 0px;
    padding: 8px 6px 8px 26px;
    color: {TEXT};
    font-size: 12pt;
    font-weight: 600;
    min-height: 22px;
}}
QComboBox:hover {{
    border-bottom: 1px solid {ACCENT};
}}
QComboBox:focus {{
    border-bottom: 2px solid {ACCENT};
}}
QComboBox::drop-down {{
    width: 22px;
    border: none;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {BG};
    padding: 4px;
    outline: none;
}}
QComboBox QLineEdit {{
    background: transparent;
    border: none;
    color: {TEXT};
    font-size: 12pt;
    font-weight: 600;
    padding: 0px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}

/* --- Boutons --- */
QPushButton {{
    background-color: {BG_CARD_HI};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 10px 18px;
    font-weight: 600;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {BORDER};
    border: 1px solid {ACCENT};
}}
QPushButton:pressed {{
    background-color: #0F1A2E;
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_CARD};
}}

QPushButton#primaryButton {{
    background-color: {ACCENT};
    color: #0B1320;
    border: none;
    font-size: 10pt;
    font-weight: 800;
    padding: 10px 18px;
    border-radius: 12px;
}}
QPushButton#primaryButton:hover {{
    background-color: {ACCENT_HOT};
}}
QPushButton#primaryButton:pressed {{
    background-color: #1EA8D6;
}}

QPushButton#overlayButton {{
    background-color: {SUCCESS};
    color: #0B1320;
    border: none;
    font-size: 10pt;
    font-weight: 800;
    padding: 10px 18px;
    border-radius: 12px;
}}
QPushButton#overlayButton:hover {{
    background-color: #6BE89A;
}}
QPushButton#overlayButton:checked {{
    background-color: {DANGER};
    color: {TEXT};
}}
QPushButton#overlayButton:checked:hover {{
    background-color: #F35D5D;
}}

QToolButton#swapButton {{
    background-color: {BG_CARD_HI};
    color: {ACCENT};
    border: 1px solid {BORDER};
    border-radius: 20px;
    font-size: 14pt;
    font-weight: 800;
    min-width: 40px;
    min-height: 40px;
    max-width: 40px;
    max-height: 40px;
}}
QToolButton#swapButton:hover {{
    background-color: {BORDER};
    border: 1px solid {ACCENT};
}}

QToolButton#expandBtn {{
    background: transparent;
    color: {TEXT_DIM};
    border: none;
    font-size: 9pt;
    font-weight: 700;
    padding: 4px 8px;
    letter-spacing: 1px;
}}
QToolButton#expandBtn:hover {{
    color: {ACCENT};
}}

/* --- Checkbox (toggle chips) --- */
QCheckBox {{
    color: {TEXT_DIM};
    font-size: 9pt;
    spacing: 8px;
    padding: 4px;
}}
QCheckBox:hover {{
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background-color: {BG_CARD};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    image: none;
}}

/* --- QListWidget : la timeline de zones --- */
QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
    padding: 0px;
}}
QListWidget::item {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin: 3px 2px;
    padding: 9px 14px;
    color: {TEXT};
}}
QListWidget::item:hover {{
    background-color: {BG_CARD_HI};
    border: 1px solid {ACCENT};
}}
QListWidget::item:selected {{
    background-color: {BG_CARD_HI};
    border: 1px solid {ACCENT};
    color: {TEXT};
}}

/* Status bar */
QStatusBar {{
    background-color: {BG};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
    font-size: 8pt;
}}

/* --- Tabs principaux --- */
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QTabWidget::tab-bar {{
    alignment: left;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 10px 22px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 10pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

/* --- Progress bar (liste de course) --- */
QProgressBar {{
    background-color: {BORDER};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 4px;
}}
QProgressBar#done::chunk {{
    background-color: {SUCCESS};
}}

/* --- SpinBox (quantite) --- */
QSpinBox {{
    background-color: {BG_CARD_HI};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 8px;
    color: {TEXT};
    font-size: 10pt;
    font-weight: 600;
    min-height: 20px;
    min-width: 70px;
}}
QSpinBox:hover {{
    border: 1px solid {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {BORDER};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {ACCENT};
}}

/* Shopping row compact buttons */
QPushButton#miniBtn {{
    background-color: {BG_CARD_HI};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 11pt;
    font-weight: 800;
    min-width: 28px;
    min-height: 22px;
    max-height: 26px;
}}
QPushButton#miniBtn:hover {{
    border: 1px solid {ACCENT};
    color: {ACCENT};
}}
QPushButton#miniBtnDanger {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 10pt;
    font-weight: 800;
    min-width: 28px;
    min-height: 22px;
    max-height: 26px;
}}
QPushButton#miniBtnDanger:hover {{
    color: {DANGER};
    border: 1px solid {DANGER};
}}

QFrame#shoppingRow {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#shoppingRowDone {{
    background-color: rgba(74, 222, 128, 0.08);
    border: 1px solid {SUCCESS};
    border-radius: 10px;
}}

/* Sliders (reglages overlay) */
QSlider::groove:horizontal {{
    border: none;
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
    border: 2px solid {BG};
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_HOT};
}}

/* Anchor buttons */
QPushButton#anchorBtn {{
    background-color: {BG_CARD_HI};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 8pt;
    font-weight: 700;
    min-height: 18px;
    min-width: 26px;
}}
QPushButton#anchorBtn:hover {{
    border: 1px solid {ACCENT};
    color: {TEXT};
}}
QPushButton#anchorBtn:checked {{
    background-color: {ACCENT};
    color: {BG};
    border: 1px solid {ACCENT};
}}

/* Diagnostic panel */
QLabel#diagLabel {{
    background-color: #0A1120;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px;
    color: {TEXT_DIM};
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 8pt;
}}
"""


# ============================================================================
# Helpers visuels
# ============================================================================

def make_card(object_name: str = "card") -> QFrame:
    """Cree un QFrame style "carte"."""
    f = QFrame()
    f.setObjectName(object_name)
    f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return f


def make_section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionLabel")
    return lbl


def _fmt_silver(value: int) -> str:
    """Formatte un montant silver en chaine lisible : 1.2k, 3.4M, etc."""
    v = int(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000_000:
        return f"{sign}{v / 1_000_000_000:.2f}G silver"
    if v >= 1_000_000:
        return f"{sign}{v / 1_000_000:.2f}M silver"
    if v >= 1_000:
        return f"{sign}{v / 1_000:.1f}k silver"
    return f"{sign}{v} silver"


class RouteStepItem(QListWidgetItem):
    """Ligne de la timeline : un numero + un nom + une pastille de danger.

    On formate tout en texte plat dans le ListWidget (simple et robuste pour
    les polices Windows), la couleur des items est geree via setForeground().
    """

    def __init__(self, index: int, name: str, color: str, tier: int, biome: str) -> None:
        arrow = "▸" if index > 0 else "◉"
        label = ZONE_LABEL.get(color, color.upper())
        text = f"  {arrow}  {index + 1:>2}   {name}       ·  T{tier}  ·  {biome}   ·  {label}"
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, name)
        self.setForeground(QColor(ZONE_COLOR.get(color, TEXT)))
        f = QFont("Segoe UI", 11)
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)


# ============================================================================
# Fenetre principale
# ============================================================================

class AlbionGPSWindow(QMainWindow):
    # Signal utilise pour remonter les changements de zone depuis le thread
    # du sniffer Photon vers le thread UI Qt (obligatoire : on ne peut pas
    # toucher aux widgets depuis un thread non-UI).
    zone_detected = pyqtSignal(str)
    sniffer_error = pyqtSignal(str)
    # object plutot que MarketEvent pour eviter d'importer depuis Qt meta
    market_event_detected = pyqtSignal(object)

    def __init__(self, world: WorldGraph, world_index: WorldIndex) -> None:
        super().__init__()
        self.world = world
        self.world_index = world_index
        self.route: Route | None = None
        self.current_zone: str | None = None

        self.detector = GameDetector()
        self.overlay = GPSOverlay(config_path=OVERLAY_CONFIG_PATH)
        self.shopping = ShoppingList.load(SHOPPING_LIST_PATH)
        self.overlay_active = False
        self.sniffer = PhotonSniffer(
            world_index=world_index,
            on_zone_change=self._on_sniffer_zone,
            on_error=self._on_sniffer_error,
            # Detection auto marche desactivee : l'event Photon d'achat
            # n'a pas pu etre identifie de facon fiable. L'utilisateur
            # saisit son prix unitaire a la main dans la liste de course.
            on_market_event=None,
            dump_path=DUMP_PATH,
        )
        self.zone_detected.connect(self._handle_detected_zone)
        self.sniffer_error.connect(self._handle_sniffer_error)

        self._sniffer_stats_timer = QTimer(self)
        self._sniffer_stats_timer.setInterval(1000)
        self._sniffer_stats_timer.timeout.connect(self._refresh_sniffer_stats)

        # Timer game-window poll (maj du chip "jeu detecte")
        self._game_poll = QTimer(self)
        self._game_poll.setInterval(1500)
        self._game_poll.timeout.connect(self._refresh_game_status)
        self._game_poll.start()

        self.setWindowTitle("Albion GPS")
        self.resize(820, 780)
        self.setMinimumSize(720, 640)

        self._build_ui()

        # Defaut : Bridgewatch -> Caerleon
        if "Bridgewatch" in world.zones and "Caerleon" in world.zones:
            self.from_combo.setCurrentText("Bridgewatch")
            self.to_combo.setCurrentText("Caerleon")

        self._refresh_game_status()

    # ================================================================ UI

    def _make_search_combo(self, items: list[str], placeholder: str) -> QComboBox:
        """Combobox editable avec auto-completion 'contient' case-insensitive."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(items)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText(placeholder)
        combo.lineEdit().setClearButtonEnabled(False)

        completer = QCompleter(items, combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        return combo

    def _build_settings_panel(self) -> QFrame:
        """Panneau de reglages de l'overlay : opacite, taille, ancrage, offsets."""
        cfg = self.overlay.config

        panel = QFrame()
        panel.setObjectName("softCard")
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        def mk_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("sectionLabel")
            return lbl

        def mk_value_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {TEXT}; font-size: 9pt; font-weight: 700;")
            lbl.setMinimumWidth(44)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return lbl

        # --- Opacite ---
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(15, 100)
        self.opacity_slider.setValue(int(cfg.opacity * 100))
        self.opacity_value = mk_value_label(f"{int(cfg.opacity * 100)}%")
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)

        grid.addWidget(mk_label("OPACITE"), 0, 0)
        grid.addWidget(self.opacity_slider, 0, 1)
        grid.addWidget(self.opacity_value, 0, 2)

        # --- Taille ---
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(50, 150)
        self.scale_slider.setValue(int(cfg.scale * 100))
        self.scale_value = mk_value_label(f"{int(cfg.scale * 100)}%")
        self.scale_slider.valueChanged.connect(self._on_scale_changed)

        grid.addWidget(mk_label("TAILLE"), 1, 0)
        grid.addWidget(self.scale_slider, 1, 1)
        grid.addWidget(self.scale_value, 1, 2)

        # --- Decalage X ---
        self.offx_slider = QSlider(Qt.Orientation.Horizontal)
        self.offx_slider.setRange(0, 600)
        self.offx_slider.setValue(cfg.offset_x)
        self.offx_value = mk_value_label(f"{cfg.offset_x}px")
        self.offx_slider.valueChanged.connect(self._on_offx_changed)

        grid.addWidget(mk_label("DECAL. X"), 2, 0)
        grid.addWidget(self.offx_slider, 2, 1)
        grid.addWidget(self.offx_value, 2, 2)

        # --- Decalage Y ---
        self.offy_slider = QSlider(Qt.Orientation.Horizontal)
        self.offy_slider.setRange(0, 600)
        self.offy_slider.setValue(cfg.offset_y)
        self.offy_value = mk_value_label(f"{cfg.offset_y}px")
        self.offy_slider.valueChanged.connect(self._on_offy_changed)

        grid.addWidget(mk_label("DECAL. Y"), 3, 0)
        grid.addWidget(self.offy_slider, 3, 1)
        grid.addWidget(self.offy_value, 3, 2)

        # --- Ancre (coin) ---
        anchor_row = QHBoxLayout()
        anchor_row.setSpacing(6)
        self._anchor_buttons: dict[str, QPushButton] = {}
        for code, label in [("TL", "↖"), ("TR", "↗"), ("BL", "↙"), ("BR", "↘")]:
            btn = QPushButton(label)
            btn.setObjectName("anchorBtn")
            btn.setCheckable(True)
            btn.setChecked(cfg.anchor == code)
            btn.clicked.connect(lambda _checked, c=code: self._on_anchor_changed(c))
            self._anchor_buttons[code] = btn
            anchor_row.addWidget(btn)
        anchor_row.addStretch(1)
        reset_btn = QPushButton("REINITIALISER")
        reset_btn.clicked.connect(self._on_reset_overlay_config)
        anchor_row.addWidget(reset_btn)

        grid.addWidget(mk_label("COIN"), 4, 0)
        grid.addLayout(anchor_row, 4, 1, 1, 2)

        return panel

    # ---- overlay settings handlers ----
    def _on_opacity_changed(self, v: int) -> None:
        self.opacity_value.setText(f"{v}%")
        self.overlay.update_config(opacity=v / 100.0)

    def _on_scale_changed(self, v: int) -> None:
        self.scale_value.setText(f"{v}%")
        self.overlay.update_config(scale=v / 100.0)

    def _on_offx_changed(self, v: int) -> None:
        self.offx_value.setText(f"{v}px")
        self.overlay.update_config(offset_x=v)

    def _on_offy_changed(self, v: int) -> None:
        self.offy_value.setText(f"{v}px")
        self.overlay.update_config(offset_y=v)

    def _on_anchor_changed(self, code: str) -> None:
        for c, btn in self._anchor_buttons.items():
            btn.setChecked(c == code)
        self.overlay.update_config(anchor=code)

    def _on_reset_overlay_config(self) -> None:
        from .overlay import OverlayConfig
        defaults = OverlayConfig()
        self.opacity_slider.setValue(int(defaults.opacity * 100))
        self.scale_slider.setValue(int(defaults.scale * 100))
        self.offx_slider.setValue(defaults.offset_x)
        self.offy_slider.setValue(defaults.offset_y)
        for c, btn in self._anchor_buttons.items():
            btn.setChecked(c == defaults.anchor)
        self.overlay.update_config(
            opacity=defaults.opacity,
            scale=defaults.scale,
            offset_x=defaults.offset_x,
            offset_y=defaults.offset_y,
            anchor=defaults.anchor,
        )

    def _on_settings_toggled(self, checked: bool) -> None:
        self.settings_panel.setVisible(checked)
        self.settings_toggle.setText("MASQUER REGLAGES ▴" if checked else "REGLER L'OVERLAY ▾")

    # =========================================================== shopping list

    def _build_shopping_page(self) -> QWidget:
        """Onglet 'Liste de course' : formulaire d'ajout + liste avec
        barres de progression pour chaque entree."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        # --- Carte : formulaire d'ajout -----------------------------------
        add_card = make_card("card")
        add_v = QVBoxLayout(add_card)
        add_v.setContentsMargins(18, 14, 18, 14)
        add_v.setSpacing(10)

        add_v.addWidget(make_section_label("Ajouter un item"))

        # Categorie
        self.cat_combo = QComboBox()
        for cat in categories():
            self.cat_combo.addItem(cat)
        self.cat_combo.currentTextChanged.connect(self._on_shop_category_changed)

        # Type (ex. Bois, Epee, Armure plate)
        self.kind_combo = QComboBox()
        self.kind_combo.currentIndexChanged.connect(self._on_shop_kind_changed)

        # Tier
        self.tier_combo = QComboBox()

        # Enchantement
        self.ench_combo = QComboBox()

        # Quantite
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 99999)
        self.qty_spin.setValue(100)
        self.qty_spin.setSingleStep(10)

        add_btn = QPushButton("AJOUTER")
        add_btn.setObjectName("primaryButton")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._on_shop_add_clicked)

        def labeled(lbl: str, w: QWidget, stretch: int = 1) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(2)
            l = QLabel(lbl)
            l.setObjectName("sectionLabel")
            col.addWidget(l)
            col.addWidget(w)
            return col

        form = QHBoxLayout()
        form.setSpacing(10)
        form.addLayout(labeled("CATEGORIE", self.cat_combo), 2)
        form.addLayout(labeled("ITEM", self.kind_combo), 3)
        form.addLayout(labeled("TIER", self.tier_combo), 1)
        form.addLayout(labeled("ENCHANT.", self.ench_combo), 1)
        form.addLayout(labeled("QUANTITE", self.qty_spin), 1)

        add_v.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(add_btn)
        add_v.addLayout(btn_row)

        layout.addWidget(add_card)

        # --- Carte : liste des items --------------------------------------
        list_card = make_card("card")
        list_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_v = QVBoxLayout(list_card)
        list_v.setContentsMargins(18, 14, 18, 14)
        list_v.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(make_section_label("Items a acheter"))
        header.addStretch(1)
        self.shop_stats_label = QLabel("")
        self.shop_stats_label.setObjectName("summarySub")
        header.addWidget(self.shop_stats_label)

        clear_done_btn = QPushButton("TERMINES")
        clear_done_btn.setToolTip("Retirer les items termines")
        clear_done_btn.clicked.connect(self._on_shop_clear_done)
        clear_all_btn = QPushButton("TOUT VIDER")
        clear_all_btn.clicked.connect(self._on_shop_clear_all)
        header.addWidget(clear_done_btn)
        header.addWidget(clear_all_btn)
        list_v.addLayout(header)

        self.shop_list_widget = QListWidget()
        self.shop_list_widget.setSpacing(2)
        self.shop_list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.shop_list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.shop_list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        list_v.addWidget(self.shop_list_widget, 1)

        layout.addWidget(list_card, 1)

        # Initialisation : categorie -> kind -> tier -> ench
        self._on_shop_category_changed(self.cat_combo.currentText())
        self._refresh_shop_list()

        return page

    # ---- handlers shopping ----

    def _on_shop_category_changed(self, cat: str) -> None:
        self.kind_combo.blockSignals(True)
        self.kind_combo.clear()
        for k in kinds_in_category(cat):
            self.kind_combo.addItem(k.name, k.key)
        self.kind_combo.blockSignals(False)
        if self.kind_combo.count() > 0:
            self._on_shop_kind_changed(0)

    def _on_shop_kind_changed(self, _index: int) -> None:
        key = self.kind_combo.currentData()
        if key is None:
            return
        kind = KIND_BY_KEY.get(key)
        if kind is None:
            return
        # Tiers
        self.tier_combo.clear()
        for t in tiers_for(kind):
            self.tier_combo.addItem(f"T{t}", t)
        # Enchantements
        self.ench_combo.clear()
        for e in enchants_for(kind):
            self.ench_combo.addItem(f".{e}" if e > 0 else ".0", e)
        self.ench_combo.setEnabled(kind.enchantable)

    def _on_shop_add_clicked(self) -> None:
        key = self.kind_combo.currentData()
        if key is None:
            return
        tier = int(self.tier_combo.currentData() or 1)
        ench = int(self.ench_combo.currentData() or 0)
        qty = self.qty_spin.value()
        self.shopping.add(key, tier, ench, qty)
        self._persist_shopping()
        self._refresh_shop_list()

    def _on_shop_clear_done(self) -> None:
        removed = self.shopping.clear_done()
        if removed > 0:
            self._persist_shopping()
            self._refresh_shop_list()

    def _on_shop_clear_all(self) -> None:
        self.shopping.clear_all()
        self._persist_shopping()
        self._refresh_shop_list()

    def _refresh_shop_list(self) -> None:
        """Rebuilds la liste d'items en widgets. Appele apres chaque mutation."""
        self.shop_list_widget.clear()

        for entry in self.shopping.entries:
            row = self._make_shop_row(entry)
            item = QListWidgetItem()
            # Row a 4 lignes : titre + progress + prix/sous-total + controles
            hint = row.sizeHint()
            hint.setHeight(max(140, hint.height()))
            item.setSizeHint(hint)
            # On desactive l'effet de selection / hover de QListWidget pour
            # ces rows custom (on a deja le fond dans le QFrame)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.shop_list_widget.addItem(item)
            self.shop_list_widget.setItemWidget(item, row)

        total, done, units = self.shopping.stats()
        budget = self.shopping.total_budget()
        remaining = self.shopping.total_remaining()
        if total == 0:
            self.shop_stats_label.setText("Liste vide")
        else:
            base = f"{done}/{total} termines  ·  {units} unites cibles"
            if budget > 0:
                base += (
                    f"  ·  budget {_fmt_silver(budget)}"
                    f"  ·  reste {_fmt_silver(remaining)}"
                )
            self.shop_stats_label.setText(base)

    def _make_shop_row(self, entry: ShoppingEntry) -> QWidget:
        """Construit la widget-ligne d'une entree : nom + progress + controles."""
        row = QFrame()
        row.setObjectName("shoppingRowDone" if entry.done else "shoppingRow")
        # 4 lignes : titre / progress / prix+total / controles
        row.setMinimumHeight(140)

        outer = QVBoxLayout(row)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # ---- Ligne 1 : nom + compteur --------------------------------
        header = QHBoxLayout()
        header.setSpacing(10)
        header.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel(format_item(entry.kind, entry.tier, entry.ench))
        name_label.setStyleSheet(
            f"color: {ACCENT if entry.done else TEXT}; "
            f"font-size: 12pt; font-weight: 800; background: transparent;"
        )
        name_label.setMinimumHeight(22)
        header.addWidget(name_label, 1)

        counter = QLabel(f"{entry.current} / {entry.target}")
        counter.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11pt; font-weight: 700; background: transparent;"
        )
        counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(counter, 0)

        outer.addLayout(header)

        # ---- Ligne 2 : progress bar ----------------------------------
        pbar = QProgressBar()
        pbar.setRange(0, max(1, entry.target))
        pbar.setValue(entry.current)
        pbar.setTextVisible(False)
        pbar.setFixedHeight(8)
        if entry.done:
            pbar.setObjectName("done")
        outer.addWidget(pbar)

        # ---- Ligne 3 : prix unitaire editable + sous-total ----------
        price_row = QHBoxLayout()
        price_row.setSpacing(8)
        price_row.setContentsMargins(0, 2, 0, 0)

        price_lbl = QLabel("Prix/u :")
        price_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10pt; font-weight: 600; "
            f"background: transparent;"
        )
        price_row.addWidget(price_lbl, 0)

        price_edit = QLineEdit()
        price_edit.setObjectName("shopPriceEdit")
        price_edit.setText(str(entry.unit_cost) if entry.unit_cost > 0 else "")
        price_edit.setPlaceholderText("0")
        price_edit.setValidator(QIntValidator(0, 99_999_999, price_edit))
        price_edit.setMaximumWidth(100)
        price_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        price_edit.editingFinished.connect(
            lambda ed=price_edit, ident=entry.ident: self._shop_set_price(ident, ed.text())
        )
        price_row.addWidget(price_edit, 0)

        silver_unit_lbl = QLabel("silver")
        silver_unit_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9pt; background: transparent;"
        )
        price_row.addWidget(silver_unit_lbl, 0)

        price_row.addStretch(1)

        # Sous-total ligne : unit_cost * target (budget total pour finir)
        if entry.unit_cost > 0 and entry.target > 0:
            sub_txt = f"Total : {_fmt_silver(entry.line_budget)}"
            if entry.current > 0 and not entry.done:
                sub_txt += f"  ·  reste {_fmt_silver(entry.line_remaining)}"
        else:
            sub_txt = ""
        subtotal_lbl = QLabel(sub_txt)
        subtotal_lbl.setStyleSheet(
            f"color: {ACCENT if entry.done else TEXT}; font-size: 10pt; "
            f"font-weight: 700; background: transparent;"
        )
        subtotal_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        price_row.addWidget(subtotal_lbl, 0)

        outer.addLayout(price_row)

        # ---- Ligne 3 : controles compacts ----------------------------
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.setContentsMargins(0, 2, 0, 0)
        controls.addStretch(1)

        minus_btn = QPushButton("−10")
        minus_btn.setObjectName("miniBtn")
        minus_btn.clicked.connect(lambda: self._shop_bump(entry.ident, -10))

        plus_btn = QPushButton("+10")
        plus_btn.setObjectName("miniBtn")
        plus_btn.clicked.connect(lambda: self._shop_bump(entry.ident, +10))

        check_btn = QPushButton("✓")
        check_btn.setObjectName("miniBtn")
        check_btn.setToolTip("Marquer comme fini / remettre a zero")
        check_btn.clicked.connect(lambda: self._shop_toggle(entry.ident))

        del_btn = QPushButton("×")
        del_btn.setObjectName("miniBtnDanger")
        del_btn.setToolTip("Supprimer de la liste")
        del_btn.clicked.connect(lambda: self._shop_remove(entry.ident))

        for b in (minus_btn, plus_btn, check_btn, del_btn):
            controls.addWidget(b)

        outer.addLayout(controls)

        return row

    def _shop_bump(self, ident: tuple, delta: int) -> None:
        self.shopping.bump_current(ident, delta)
        self._persist_shopping()
        self._refresh_shop_list()

    def _shop_set_price(self, ident: tuple, raw_text: str) -> None:
        """Handler appele quand l'utilisateur edite le prix unitaire."""
        try:
            cost = int((raw_text or "0").strip() or "0")
        except ValueError:
            cost = 0
        self.shopping.set_unit_cost(ident, cost)
        self._persist_shopping()
        self._refresh_shop_list()

    def _shop_toggle(self, ident: tuple) -> None:
        self.shopping.toggle_done(ident)
        self._persist_shopping()
        self._refresh_shop_list()

    def _shop_remove(self, ident: tuple) -> None:
        self.shopping.remove(ident)
        self._persist_shopping()
        self._refresh_shop_list()

    def _persist_shopping(self) -> None:
        self.shopping.save(SHOPPING_LIST_PATH)

    # ================================================================ UI

    def _build_ui(self) -> None:
        # ---- Header -------------------------------------------------------
        title = QLabel("ALBION GPS")
        title.setObjectName("appTitle")
        subtitle = QLabel("NAVIGATION TEMPS REEL")
        subtitle.setObjectName("appSubtitle")

        header_text = QVBoxLayout()
        header_text.setSpacing(0)
        header_text.addWidget(title)
        header_text.addWidget(subtitle)

        self.game_chip = QLabel("JEU NON DETECTE")
        self.game_chip.setObjectName("statusChipOff")
        self.game_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        header.addLayout(header_text)
        header.addStretch(1)
        header.addWidget(self.game_chip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # ---- Carte planificateur -----------------------------------------
        planner = make_card("card")
        planner_layout = QVBoxLayout(planner)
        planner_layout.setContentsMargins(18, 12, 18, 14)
        planner_layout.setSpacing(8)

        planner_layout.addWidget(make_section_label("Planifier un trajet"))

        # Ligne depart / swap / arrivee : comboboxes editables avec
        # auto-completion. On attache un QCompleter custom (case-insensitive,
        # match "contient" et pas seulement "commence par") pour que l'user
        # puisse taper n'importe quel fragment du nom de zone.
        zone_names = list(self.world.zone_names())
        self.from_combo = self._make_search_combo(zone_names, "Rechercher une zone...")
        self.to_combo = self._make_search_combo(zone_names, "Rechercher une zone...")

        from_col = QVBoxLayout()
        from_col.setSpacing(4)
        lbl_from = QLabel("DEPART")
        lbl_from.setObjectName("sectionLabel")
        from_col.addWidget(lbl_from)
        from_col.addWidget(self.from_combo)

        to_col = QVBoxLayout()
        to_col.setSpacing(4)
        lbl_to = QLabel("ARRIVEE")
        lbl_to.setObjectName("sectionLabel")
        to_col.addWidget(lbl_to)
        to_col.addWidget(self.to_combo)

        self.swap_button = QToolButton()
        self.swap_button.setObjectName("swapButton")
        self.swap_button.setText("⇅")
        self.swap_button.setToolTip("Inverser depart et arrivee")
        self.swap_button.clicked.connect(self.on_swap)

        fields_row = QHBoxLayout()
        fields_row.setSpacing(10)
        fields_row.addLayout(from_col, 1)
        # petit wrapper pour centrer verticalement le bouton swap avec les pills
        swap_wrap = QVBoxLayout()
        swap_wrap.addSpacing(14)
        swap_wrap.addWidget(self.swap_button, 0, Qt.AlignmentFlag.AlignCenter)
        fields_row.addLayout(swap_wrap)
        fields_row.addLayout(to_col, 1)

        planner_layout.addLayout(fields_row)

        # Controles : safe only + CTA
        self.safe_only_box = QCheckBox("Routes sures uniquement (Blue + Yellow)")
        self.safe_only_box.setChecked(True)

        self.compute_button = QPushButton("CALCULER L'ITINERAIRE")
        self.compute_button.setObjectName("primaryButton")
        self.compute_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compute_button.clicked.connect(self.on_compute)

        controls = QHBoxLayout()
        controls.addWidget(self.safe_only_box)
        controls.addStretch(1)
        controls.addWidget(self.compute_button)
        planner_layout.addLayout(controls)

        # ---- Bande resume du trajet --------------------------------------
        self.summary_card = make_card("routeStripSafe")
        summary_layout = QHBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(18, 8, 18, 8)
        summary_layout.setSpacing(14)

        self.summary_main = QLabel("Aucun itineraire calcule")
        self.summary_main.setObjectName("summaryMain")
        self.summary_sub = QLabel("Choisis un depart et une arrivee, puis clique Calculer.")
        self.summary_sub.setObjectName("summarySub")

        sum_text = QVBoxLayout()
        sum_text.setSpacing(2)
        sum_text.addWidget(self.summary_main)
        sum_text.addWidget(self.summary_sub)
        summary_layout.addLayout(sum_text, 1)

        # ---- Timeline des zones ------------------------------------------
        timeline_card = make_card("card")
        timeline_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(18, 14, 18, 14)
        timeline_layout.setSpacing(8)

        tl_header = QHBoxLayout()
        tl_header.addWidget(make_section_label("Etapes du trajet"))
        tl_header.addStretch(1)
        self.hint_label = QLabel("Clique une etape pour corriger la zone actuelle")
        self.hint_label.setObjectName("summarySub")
        tl_header.addWidget(self.hint_label)
        timeline_layout.addLayout(tl_header)

        self.result_list = QListWidget()
        self.result_list.setSpacing(2)
        self.result_list.setFrameShape(QFrame.Shape.NoFrame)
        self.result_list.itemClicked.connect(self.on_zone_clicked)
        timeline_layout.addWidget(self.result_list, 1)

        # ---- Footer navigation -------------------------------------------
        nav_card = make_card("card")
        nav_layout = QVBoxLayout(nav_card)
        nav_layout.setContentsMargins(18, 10, 18, 10)
        nav_layout.setSpacing(6)

        # Ligne 1 : zone courante + overlay
        lbl_current_title = QLabel("ZONE ACTUELLE")
        lbl_current_title.setObjectName("currentZoneLabel")
        self.current_zone_label = QLabel("—")
        self.current_zone_label.setObjectName("currentZoneName")

        current_col = QVBoxLayout()
        current_col.setSpacing(2)
        current_col.addWidget(lbl_current_title)
        current_col.addWidget(self.current_zone_label)

        self.overlay_button = QPushButton("ACTIVER OVERLAY IN-GAME")
        self.overlay_button.setObjectName("overlayButton")
        self.overlay_button.setCheckable(True)
        self.overlay_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_button.toggled.connect(self.on_toggle_overlay)

        nav_line = QHBoxLayout()
        nav_line.addLayout(current_col, 1)
        nav_line.addWidget(self.overlay_button, 0, Qt.AlignmentFlag.AlignVCenter)
        nav_layout.addLayout(nav_line)

        # Ligne 2 : autodetect toggle + boutons reglages / diag
        self.auto_detect_box = QCheckBox("Detection auto (Photon)")
        self.auto_detect_box.setChecked(True)
        self.auto_detect_box.toggled.connect(self.on_toggle_autodetect)

        self.settings_toggle = QToolButton()
        self.settings_toggle.setObjectName("expandBtn")
        self.settings_toggle.setText("REGLER L'OVERLAY ▾")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.toggled.connect(self._on_settings_toggled)

        self.diag_toggle = QToolButton()
        self.diag_toggle.setObjectName("expandBtn")
        self.diag_toggle.setText("DIAGNOSTIC ▾")
        self.diag_toggle.setCheckable(True)
        self.diag_toggle.toggled.connect(self._on_diag_toggled)

        nav_line2 = QHBoxLayout()
        nav_line2.addWidget(self.auto_detect_box)
        nav_line2.addStretch(1)
        nav_line2.addWidget(self.settings_toggle)
        nav_line2.addWidget(self.diag_toggle)
        nav_layout.addLayout(nav_line2)

        # Panneau reglages overlay (replie par defaut)
        self.settings_panel = self._build_settings_panel()
        self.settings_panel.setVisible(False)
        nav_layout.addWidget(self.settings_panel)

        # Diagnostic repliable
        self.diag_label = QLabel("Auto-detect : inactif")
        self.diag_label.setObjectName("diagLabel")
        self.diag_label.setWordWrap(True)
        self.diag_label.setVisible(False)
        self.diag_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        nav_layout.addWidget(self.diag_label)

        # ---- Assemblage onglet Navigation --------------------------------
        nav_page = QWidget()
        nav_page_layout = QVBoxLayout(nav_page)
        nav_page_layout.setContentsMargins(0, 4, 0, 0)
        nav_page_layout.setSpacing(8)
        nav_page_layout.addWidget(planner, 0)
        nav_page_layout.addWidget(self.summary_card, 0)
        nav_page_layout.addWidget(timeline_card, 10)
        nav_page_layout.addWidget(nav_card, 0)

        # ---- Assemblage onglet Liste de course ---------------------------
        shopping_page = self._build_shopping_page()

        # ---- Tabs --------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(nav_page, "NAVIGATION")
        self.tabs.addTab(shopping_page, "LISTE DE COURSE")

        root = QVBoxLayout()
        root.setContentsMargins(18, 12, 18, 10)
        root.setSpacing(8)
        root.addLayout(header)
        root.addWidget(self.tabs, 1)

        container = QWidget()
        container.setObjectName("centralContainer")
        container.setLayout(root)
        self.setCentralWidget(container)

        # Status bar discrete pour les metadata du graphe charge
        self.statusBar().showMessage(
            f"  {len(self.world.zones)} zones  ·  "
            f"{sum(len(v) for v in self.world.adjacency.values()) // 2} connexions  ·  "
            "Albion GPS"
        )

    # =============================================================== actions

    def on_swap(self) -> None:
        a = self.from_combo.currentText()
        b = self.to_combo.currentText()
        self.from_combo.setCurrentText(b)
        self.to_combo.setCurrentText(a)

    def on_compute(self) -> None:
        start = self.from_combo.currentText()
        goal = self.to_combo.currentText()
        safe = self.safe_only_box.isChecked()

        route = self.world.find_route(start, goal, safe_only=safe)
        self.result_list.clear()
        self.route = route
        self.current_zone = None
        self.current_zone_label.setText("—")

        if route is None:
            mode = "sure (blue + yellow)" if safe else "toutes zones"
            self.summary_main.setText("Aucun itineraire trouve")
            self.summary_sub.setText(
                f"Pas de chemin {mode} entre {start} et {goal}. "
                "Essaie de decocher 'Routes sures uniquement'."
            )
            self._set_summary_style("warn")
            self._push_overlay_state()
            return

        nb_yellow = sum(1 for c in route.crossed_colors if c == "yellow")
        nb_red = sum(1 for c in route.crossed_colors if c == "red")
        nb_black = sum(1 for c in route.crossed_colors if c == "black")

        self.summary_main.setText(
            f"{route.total_hops} sauts  ·  {len(route.path)} zones"
        )
        if nb_red + nb_black > 0:
            self.summary_sub.setText(
                f"⚠ PvP detecte : {nb_red} rouge / {nb_black} noir / {nb_yellow} jaune"
            )
            self._set_summary_style("danger")
        elif nb_yellow > 0:
            self.summary_sub.setText(
                f"Attention : {nb_yellow} zone(s) jaune(s) sur le trajet"
            )
            self._set_summary_style("warn")
        else:
            self.summary_sub.setText("Trajet 100% sur (Blue uniquement)")
            self._set_summary_style("safe")

        for i, name in enumerate(route.path):
            zone = self.world.zones[name]
            item = RouteStepItem(i, name, zone.color, zone.tier, zone.biome)
            self.result_list.addItem(item)

        if self.result_list.count() > 0:
            self.result_list.setCurrentRow(0)
            self.on_zone_clicked(self.result_list.item(0))

    def _set_summary_style(self, kind: str) -> None:
        mapping = {
            "safe":   "routeStripSafe",
            "warn":   "routeStripWarn",
            "danger": "routeStripDanger",
        }
        self.summary_card.setObjectName(mapping.get(kind, "routeStripSafe"))
        # Forcer Qt a re-appliquer la feuille de style
        self.summary_card.style().unpolish(self.summary_card)
        self.summary_card.style().polish(self.summary_card)

    def on_zone_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name is None:
            return
        self.current_zone = name
        self.current_zone_label.setText(name)
        self._push_overlay_state()

    def on_toggle_overlay(self, checked: bool) -> None:
        if checked:
            if self.overlay.start():
                self.overlay_active = True
                self.overlay_button.setText("DESACTIVER OVERLAY")
                self._push_overlay_state()
                if self.auto_detect_box.isChecked():
                    self._start_sniffer()
            else:
                self.overlay_button.setChecked(False)
                self.game_chip.setText("LANCE ALBION D'ABORD")
        else:
            self.overlay.stop()
            self.overlay_active = False
            self.overlay_button.setText("ACTIVER OVERLAY IN-GAME")
            self._stop_sniffer()
        self._refresh_game_status()

    def on_toggle_autodetect(self, checked: bool) -> None:
        if checked and self.overlay_active:
            self._start_sniffer()
        elif not checked:
            self._stop_sniffer()

    def _on_diag_toggled(self, checked: bool) -> None:
        self.diag_label.setVisible(checked)
        self.diag_toggle.setText("DIAGNOSTIC ▴" if checked else "DIAGNOSTIC ▾")

    # =============================================================== auto-detect

    def _start_sniffer(self) -> None:
        if self.sniffer.is_running:
            return
        self.sniffer.start()
        self._sniffer_stats_timer.start()
        self.diag_label.setText("Auto-detect : demarrage...")

    def _stop_sniffer(self) -> None:
        if not self.sniffer.is_running:
            return
        self.sniffer.stop()
        self._sniffer_stats_timer.stop()
        self.diag_label.setText("Auto-detect : inactif")

    def _refresh_sniffer_stats(self) -> None:
        if not self.sniffer.is_running:
            return
        st = self.sniffer.extended_stats
        seen = st["packets_seen"]
        with_zone = st["packets_with_zone"]
        decoded = st["messages_decoded"]
        n_ifaces = len(st["ifaces"])
        top_tuples = st.get("top_tuples") or []

        # Diagnostic marche desactive : la detection auto est off.
        market_block = ""

        if seen == 0:
            msg = (
                f"Auto-detect : {n_ifaces} iface(s) ecoutees, 0 paquet. "
                "Traverse un portail."
            )
        elif decoded == 0:
            msg = (
                f"Auto-detect : {seen} paquets recus, 0 message Photon decode. "
                "Protocole inhabituel ?"
            )
        elif with_zone == 0:
            raw_total = st.get("raw_strings_total", 0)
            raw_sample = st.get("raw_sample") or []
            sample_str = " | ".join(raw_sample[:4]) if raw_sample else "(aucune)"
            ev = st.get("msg_events", 0)
            oreq = st.get("msg_op_requests", 0)
            oresp = st.get("msg_op_responses", 0)
            frag_r = st.get("fragments_received", 0)
            frag_a = st.get("groups_assembled", 0)
            op_samples = st.get("op_response_samples") or []
            op_str = "\n  ".join(op_samples) if op_samples else "(aucune)"
            msg = (
                f"Auto-detect : {seen} pkts, {decoded} msgs, 0 cluster matche. "
                f"Dump = {st['dump_bytes'] // 1024} KB.\n"
                f"Messages : ev={ev}, op_req={oreq}, op_resp={oresp} | "
                f"Frags : recus={frag_r}, assembled={frag_a} | "
                f"RAW strings={raw_total}\n"
                f"Op_responses :\n  {op_str}\n"
                f"Echantillon strings : {sample_str}"
            )
        else:
            recent = st.get("recent_matches") or []
            top_str = "\n  ".join(top_tuples) if top_tuples else "(aucun)"
            recent_str = "\n  ".join(recent) if recent else "(aucun)"
            msg = (
                f"Auto-detect : {seen} pkts / {decoded} msgs / {with_zone} matchs\n"
                f"Candidats :\n  {top_str}\n"
                f"Derniers matchs :\n  {recent_str}"
            )

        self.diag_label.setText(msg)

    def _on_sniffer_zone(self, zone: str) -> None:
        self.zone_detected.emit(zone)

    def _on_sniffer_error(self, msg: str) -> None:
        self.sniffer_error.emit(msg)

    def _on_sniffer_market_event(self, event) -> None:
        """Thread callback depuis scapy -> on remonte vers le thread UI."""
        self.market_event_detected.emit(event)

    def _handle_market_event(self, event) -> None:
        """Thread UI : event = MarketEvent. Auto-match strict + persist."""
        if event is None or event.kind_key is None:
            return
        updated = self.shopping.record_purchase(
            kind=event.kind_key,
            tier=event.tier,
            ench=event.ench,
            qty=event.quantity,
            total_silver=event.total_silver,
        )
        if updated is None:
            # Pas de match dans la liste : on ignore silencieusement pour
            # eviter de polluer l'UI avec chaque achat non cible.
            return
        self._persist_shopping()
        self._refresh_shop_list()

    def _handle_detected_zone(self, zone: str) -> None:
        self.current_zone = zone
        self.current_zone_label.setText(f"{zone}  ·  AUTO")

        # Selection dans la liste si la zone fait partie du trajet
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == zone:
                self.result_list.setCurrentRow(i)
                break

        self._push_overlay_state()

    def _handle_sniffer_error(self, msg: str) -> None:
        self.diag_label.setText(f"Erreur sniffer : {msg}")
        if not self.diag_toggle.isChecked():
            self.diag_toggle.setChecked(True)

    # =============================================================== helpers

    def _refresh_game_status(self) -> None:
        win = self.detector.get_game_window()
        if win is not None:
            r = win.rect
            self.game_chip.setText(f"● JEU DETECTE  {r.width}×{r.height}")
            self.game_chip.setObjectName("statusChipOk")
        else:
            self.game_chip.setText("○ JEU NON DETECTE")
            self.game_chip.setObjectName("statusChipOff")
        # Re-applique le QSS pour que le nouveau nom prenne effet
        self.game_chip.style().unpolish(self.game_chip)
        self.game_chip.style().polish(self.game_chip)

    def _push_overlay_state(self) -> None:
        state = OverlayState()
        if self.route is not None and self.current_zone is not None:
            path = self.route.path
            if self.current_zone in path:
                idx = path.index(self.current_zone)
                if idx + 1 < len(path):
                    nxt = path[idx + 1]
                    nxt_zone = self.world.zones[nxt]
                    cur_zone = self.world.zones[self.current_zone]
                    state.next_zone = nxt
                    state.next_zone_color = nxt_zone.color
                    state.current_zone = self.current_zone
                    state.remaining_hops = len(path) - 1 - idx
                    state.direction = compute_cardinal(cur_zone.pos, nxt_zone.pos)
                else:
                    state.next_zone = None
                    state.remaining_hops = 0
        self.overlay.update_state(state)

    def closeEvent(self, event) -> None:  # noqa: D401
        self.overlay.stop()
        self.sniffer.stop()
        super().closeEvent(event)


# ============================================================================
# Entry point
# ============================================================================

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_QSS)

    world = WorldGraph.from_json(DATA_FILE)
    try:
        world_index = WorldIndex.from_xml(WORLD_XML)
    except Exception as e:
        print(f"[WARN] Impossible de charger world.xml : {e}")
        world_index = WorldIndex()
    window = AlbionGPSWindow(world, world_index)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
