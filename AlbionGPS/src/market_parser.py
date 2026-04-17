"""
Detection des achats au marche via sniffing Photon.

Albion Online envoie des events Photon quand une transaction de marche est
confirmee (event du type EvAuctionBuyOfferFinished ou equivalent). Les codes
d'events Photon changent a chaque patch du jeu donc on ne peut pas les
hardcoder comme on ferait pour une API stable. A la place on detecte par
SIGNATURE, comme on l'a fait pour les zones avec les tuples ground-truth :

  1. au moins un parametre string qui matche l'ID d'item Albion (T{n}_XXX[@N])
  2. au moins deux parametres int positifs (candidats qty et prix)
  3. contraintes de vraisemblance sur qty (<=10000) et prix (>= qty * 10)

Des qu'un message Photon satisfait ces trois conditions, on emet un
MarketEvent. Les faux positifs sont possibles au tout debut (equip d'un
item avec son cout silver, listing d'offres) mais ils se filtrent ensuite
dans main.py par l'auto-match strict : on n'incremente la liste de course
que si l'item correspond exactement a une entree existante.

Une fois qu'on aura collecte quelques events reels en condition de jeu, on
pourra verrouiller la detection sur le vrai (kind_message, opcode) observe
et enlever le scoring heuristique.

L'ID d'item Albion suit le format T{tier}_{body}[@{ench}] :
  - T4_WOOD               (bois brut tier 4)
  - T5_PLANKS_LEVEL1@1    (planches tier 5 enchant 1)
  - T6_MAIN_SWORD         (epee main-hand tier 6)
  - T7_2H_HAMMER@3        (marteau 2H tier 7 enchant 3)
  - T5_HEAD_CLOTH_SET1@2  (capuche tissu tier 5 enchant 2)

On mappe le "body" (la partie entre T{n}_ et @{e}) vers une kind_key de
notre catalogue resources.py. Le mapping est volontairement permissif :
s'il n'y a pas de match, on renvoie quand meme le MarketEvent avec
kind_key=None (utile pour le log d'historique).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .photon_proto import PhotonMessage


# Pattern d'ID Albion complet : T{tier}_{body}[@{ench}]
ITEM_ID_RE = re.compile(r"^T([1-8])_([A-Z0-9_]+?)(?:@([0-4]))?$")


# Mapping body -> kind_key de resources.py. Ordre important : les patterns
# specifiques d'abord, les plus generiques ensuite. Chaque entree est un
# regex teste contre le body (deja depouille de T{n}_ et de @{e}).
_BODY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ---- Matieres brutes (body == nom exact) ----
    (re.compile(r"^WOOD$"),  "WOOD"),
    (re.compile(r"^ROCK$"),  "ROCK"),
    (re.compile(r"^ORE$"),   "ORE"),
    (re.compile(r"^FIBER$"), "FIBER"),
    (re.compile(r"^HIDE$"),  "HIDE"),
    # ---- Matieres raffinees (prefixe, avec optionnel _LEVELn) ----
    (re.compile(r"^PLANKS"),     "PLANKS"),
    (re.compile(r"^STONEBLOCK"), "STONEBLOCK"),
    (re.compile(r"^METALBAR"),   "METALBAR"),
    (re.compile(r"^CLOTH$"),     "CLOTH"),
    (re.compile(r"^LEATHER$"),   "LEATHER"),
    # ---- Armures : slot_material (prefix, ignore le _SETn) ----
    (re.compile(r"^HEAD_CLOTH"),    "CLOTH_HEAD"),
    (re.compile(r"^ARMOR_CLOTH"),   "CLOTH_CHEST"),
    (re.compile(r"^SHOES_CLOTH"),   "CLOTH_BOOTS"),
    (re.compile(r"^HEAD_LEATHER"),  "LEATHER_HEAD"),
    (re.compile(r"^ARMOR_LEATHER"), "LEATHER_CHEST"),
    (re.compile(r"^SHOES_LEATHER"), "LEATHER_BOOTS"),
    (re.compile(r"^HEAD_PLATE"),    "PLATE_HEAD"),
    (re.compile(r"^ARMOR_PLATE"),   "PLATE_CHEST"),
    (re.compile(r"^SHOES_PLATE"),   "PLATE_BOOTS"),
    # ---- Armes de melee (main-hand et 2H confondus) ----
    (re.compile(r"^(?:MAIN|2H)_.*SWORD"),        "SWORD"),
    (re.compile(r"^(?:MAIN|2H)_.*AXE$"),         "AXE"),
    (re.compile(r"^(?:MAIN|2H)_.*HAMMER"),       "HAMMER"),
    (re.compile(r"^(?:MAIN|2H)_.*MACE"),         "MACE"),
    (re.compile(r"^(?:MAIN|2H)_.*WARGLOVES"),    "WAR_GLOVES"),
    (re.compile(r"^(?:MAIN|2H)_.*SPEAR"),        "SPEAR"),
    (re.compile(r"^(?:MAIN|2H)_.*DAGGER"),       "DAGGER"),
    (re.compile(r"^2H_.*QUARTERSTAFF"),          "QSTAFF"),
    # ---- Armes a distance ----
    (re.compile(r"^2H_BOW"),        "BOW"),
    (re.compile(r"^2H_CROSSBOW"),   "CROSSBOW"),
    # ---- Batons magiques (main-hand et 2H) ----
    (re.compile(r"^(?:MAIN|2H)_.*FIRE(?:STAFF|BALL)"), "FIRE_STAFF"),
    (re.compile(r"^(?:MAIN|2H)_.*FROST(?:STAFF|BOLT)"), "FROST_STAFF"),
    (re.compile(r"^(?:MAIN|2H)_.*ARCANE"),   "ARCANE_STAFF"),
    (re.compile(r"^(?:MAIN|2H)_.*HOLY"),     "HOLY_STAFF"),
    (re.compile(r"^(?:MAIN|2H)_.*NATURE"),   "NATURE_STAFF"),
    (re.compile(r"^(?:MAIN|2H)_.*CURSED"),   "CURSED_STAFF"),
    # ---- Main-gauche ----
    (re.compile(r"^OFF_SHIELD"), "SHIELD"),
    (re.compile(r"^OFF_TORCH"),  "TORCH"),
    (re.compile(r"^OFF_HORN"),   "HORN"),
    (re.compile(r"^OFF_BOOK"),   "TOME"),
    (re.compile(r"^OFF_ORB"),    "ORB"),
    # ---- Accessoires ----
    (re.compile(r"^CAPE"),    "CAPE"),
    (re.compile(r"^BAG$"),    "BAG"),
    # ---- Consommables ----
    (re.compile(r"^POTION"),  "POTION"),
    (re.compile(r"^MEAL"),    "FOOD"),
    # ---- Outils ----
    (re.compile(r"^TOOL_SICKLE"),  "TOOL_SICKLE"),
    (re.compile(r"^TOOL_PICK"),    "TOOL_PICKAXE"),
    (re.compile(r"^TOOL_AXE"),     "TOOL_AXE"),
    (re.compile(r"^TOOL_KNIFE"),   "TOOL_KNIFE"),
    (re.compile(r"^TOOL_FISH"),    "TOOL_ROD"),
]


def parse_item_id(item_id: str) -> Optional[tuple[Optional[str], int, int]]:
    """Parse un ID d'item Albion en (kind_key, tier, ench).

    Retourne None si l'ID ne matche pas le format Albion. Si le format est
    valide mais qu'aucun body pattern ne matche, retourne (None, tier, ench)
    pour qu'on puisse quand meme logger l'achat dans l'historique brut.
    """
    m = ITEM_ID_RE.match(item_id.strip())
    if not m:
        return None
    tier = int(m.group(1))
    body = m.group(2)
    ench = int(m.group(3)) if m.group(3) else 0
    for pat, key in _BODY_PATTERNS:
        if pat.match(body):
            return key, tier, ench
    return None, tier, ench


@dataclass
class MarketEvent:
    """Un achat detecte au marche (potentiellement faux-positif au debut)."""

    item_id: str               # ID Albion brut, ex: "T4_WOOD@2"
    kind_key: Optional[str]    # cle catalogue resources.py, None si inconnu
    tier: int
    ench: int
    quantity: int
    total_silver: int          # silver total paye pour l'ensemble


def _collect_ints(value: Any, out: list[int]) -> None:
    """Collecte recursivement tous les int positifs dans une valeur Photon."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if value > 0:
            out.append(value)
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            _collect_ints(v, out)
        return
    if isinstance(value, dict):
        for v in value.values():
            _collect_ints(v, out)


def _find_item_id(value: Any) -> Optional[str]:
    """Trouve la premiere string qui matche le pattern d'item ID Albion."""
    if isinstance(value, str):
        s = value.strip()
        if ITEM_ID_RE.match(s):
            return s
        return None
    if isinstance(value, (list, tuple)):
        for v in value:
            hit = _find_item_id(v)
            if hit:
                return hit
        return None
    if isinstance(value, dict):
        for v in value.values():
            hit = _find_item_id(v)
            if hit:
                return hit
        return None
    return None


def parse_market_event(msg: PhotonMessage) -> Optional[MarketEvent]:
    """Detection heuristique d'un event d'achat marche dans un message Photon.

    Retourne un MarketEvent si le message contient une signature compatible
    avec une transaction d'achat, None sinon. Conditions :
      - un parametre string qui matche l'ID d'item Albion
      - au moins 2 parametres int positifs
      - qty candidate (le plus petit int positif) entre 1 et 10000
      - silver candidate (le plus gros int positif) >= qty * 10

    Ces contraintes sont conservatrices pour limiter les faux positifs en
    mode decouverte. Une fois qu'on aura le vrai opcode on pourra enlever
    tout le scoring.
    """
    # Etape 1 : chercher un ID d'item dans les parametres
    item_id: Optional[str] = None
    ints: list[int] = []
    for value in msg.params.values():
        if item_id is None:
            hit = _find_item_id(value)
            if hit:
                item_id = hit
        _collect_ints(value, ints)

    if item_id is None or len(ints) < 2:
        return None

    parsed = parse_item_id(item_id)
    if parsed is None:
        return None
    kind_key, tier, ench = parsed

    # Etape 2 : heuristique qty vs silver
    ints.sort()
    qty = ints[0]
    silver = ints[-1]

    # Filtres de vraisemblance
    if qty < 1 or qty > 10_000:
        return None
    if silver < qty * 10:
        return None
    # Le prix par unite ne doit pas exceder 100 000 000 silver (sanity check)
    if silver > 100_000_000 * qty:
        return None

    return MarketEvent(
        item_id=item_id,
        kind_key=kind_key,
        tier=tier,
        ench=ench,
        quantity=qty,
        total_silver=silver,
    )
