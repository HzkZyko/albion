"""
Catalogue des ressources et items d'Albion Online.

On expose un ensemble plat de "ResourceKind" (types de base, ex. "Bois",
"Epee", "Armure plate torse"). Chaque kind definit :
- key     : identifiant stable stocke dans la liste de course
- name    : nom francais pour l'affichage
- category: regroupement (Raw, Refined, Weapons, Armor, ...)
- min_tier / max_tier : plage de tiers disponibles pour cet item
- enchantable : True si l'item peut avoir un enchantement .1/.2/.3/.4

Les tiers sont T1 a T8 (certains items n'existent qu'a partir d'un tier
minimum, ex. les armes apprentis a T2). L'enchantement va de 0 a 4 et
s'ecrit sous la forme "T4.3" pour tier 4 enchantement 3.

On utilise une structure en categories pour faciliter le picker hierarchique
dans l'UI (Categorie -> Famille -> Tier -> Enchant).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ResourceKind:
    key: str
    name: str
    category: str
    min_tier: int = 1
    max_tier: int = 8
    enchantable: bool = True


# ----------------------------------------------------------------------------
# Categorie : Matieres brutes (T1-T8, .0-.4)
# ----------------------------------------------------------------------------
RAW_MATERIALS: list[ResourceKind] = [
    ResourceKind("WOOD",  "Bois",    "Matieres brutes"),
    ResourceKind("ROCK",  "Pierre",  "Matieres brutes"),
    ResourceKind("ORE",   "Minerai", "Matieres brutes"),
    ResourceKind("FIBER", "Fibre",   "Matieres brutes"),
    ResourceKind("HIDE",  "Peau",    "Matieres brutes"),
]

# ----------------------------------------------------------------------------
# Categorie : Matieres raffinees (T2-T8 - on garde T1 pour simplicite)
# ----------------------------------------------------------------------------
REFINED_MATERIALS: list[ResourceKind] = [
    ResourceKind("PLANKS",     "Planches",       "Matieres raffinees"),
    ResourceKind("STONEBLOCK", "Blocs de pierre","Matieres raffinees"),
    ResourceKind("METALBAR",   "Lingots",        "Matieres raffinees"),
    ResourceKind("CLOTH",      "Tissu",          "Matieres raffinees"),
    ResourceKind("LEATHER",    "Cuir",           "Matieres raffinees"),
]

# ----------------------------------------------------------------------------
# Categorie : Armes (T2-T8)
# ----------------------------------------------------------------------------
WEAPONS: list[ResourceKind] = [
    # Armes de melee
    ResourceKind("SWORD",       "Epee",          "Armes", min_tier=2),
    ResourceKind("AXE",         "Hache",         "Armes", min_tier=2),
    ResourceKind("MACE",        "Masse",         "Armes", min_tier=2),
    ResourceKind("HAMMER",      "Marteau",       "Armes", min_tier=2),
    ResourceKind("WAR_GLOVES",  "Gantelets",     "Armes", min_tier=2),
    ResourceKind("SPEAR",       "Lance",         "Armes", min_tier=2),
    ResourceKind("DAGGER",      "Dague",         "Armes", min_tier=2),
    ResourceKind("QSTAFF",      "Baton",         "Armes", min_tier=2),
    # Armes a distance
    ResourceKind("BOW",         "Arc",           "Armes", min_tier=2),
    ResourceKind("CROSSBOW",    "Arbalete",      "Armes", min_tier=2),
    # Batons magiques
    ResourceKind("FIRE_STAFF",   "Baton de feu",      "Armes", min_tier=2),
    ResourceKind("FROST_STAFF",  "Baton de glace",    "Armes", min_tier=2),
    ResourceKind("ARCANE_STAFF", "Baton arcanique",   "Armes", min_tier=2),
    ResourceKind("HOLY_STAFF",   "Baton sacre",       "Armes", min_tier=2),
    ResourceKind("NATURE_STAFF", "Baton de la nature","Armes", min_tier=2),
    ResourceKind("CURSED_STAFF", "Baton maudit",      "Armes", min_tier=2),
]

# ----------------------------------------------------------------------------
# Categorie : Main-gauche / Off-hand (T2-T8)
# ----------------------------------------------------------------------------
OFFHANDS: list[ResourceKind] = [
    ResourceKind("SHIELD",  "Bouclier",       "Main-gauche", min_tier=2),
    ResourceKind("TORCH",   "Torche",         "Main-gauche", min_tier=2),
    ResourceKind("HORN",    "Cor de guerre",  "Main-gauche", min_tier=2),
    ResourceKind("TOME",    "Tome",           "Main-gauche", min_tier=2),
    ResourceKind("ORB",     "Orbe",           "Main-gauche", min_tier=2),
]

# ----------------------------------------------------------------------------
# Categorie : Armures (T2-T8)
# ----------------------------------------------------------------------------
ARMORS: list[ResourceKind] = [
    # Tissu
    ResourceKind("CLOTH_HEAD",  "Capuche tissu", "Armures", min_tier=2),
    ResourceKind("CLOTH_CHEST", "Robe tissu",    "Armures", min_tier=2),
    ResourceKind("CLOTH_BOOTS", "Bottes tissu",  "Armures", min_tier=2),
    # Cuir
    ResourceKind("LEATHER_HEAD",  "Capuche cuir", "Armures", min_tier=2),
    ResourceKind("LEATHER_CHEST", "Veste cuir",   "Armures", min_tier=2),
    ResourceKind("LEATHER_BOOTS", "Bottes cuir",  "Armures", min_tier=2),
    # Plaques
    ResourceKind("PLATE_HEAD",  "Casque plate", "Armures", min_tier=2),
    ResourceKind("PLATE_CHEST", "Armure plate", "Armures", min_tier=2),
    ResourceKind("PLATE_BOOTS", "Bottes plate", "Armures", min_tier=2),
]

# ----------------------------------------------------------------------------
# Categorie : Accessoires (capes, sacs) T2-T8
# ----------------------------------------------------------------------------
ACCESSORIES: list[ResourceKind] = [
    ResourceKind("CAPE", "Cape", "Accessoires", min_tier=2),
    ResourceKind("BAG",  "Sac",  "Accessoires", min_tier=2),
]

# ----------------------------------------------------------------------------
# Categorie : Consommables (potions, nourriture) T1-T8 selon l'item
# ----------------------------------------------------------------------------
CONSUMABLES: list[ResourceKind] = [
    ResourceKind("POTION", "Potion",      "Consommables", min_tier=1, enchantable=False),
    ResourceKind("FOOD",   "Nourriture",  "Consommables", min_tier=1, enchantable=False),
]

# ----------------------------------------------------------------------------
# Categorie : Outils (faucille, pioche, hache, couteau, canne a peche) T2-T8
# ----------------------------------------------------------------------------
TOOLS: list[ResourceKind] = [
    ResourceKind("TOOL_SICKLE",  "Faucille",       "Outils", min_tier=2),
    ResourceKind("TOOL_PICKAXE", "Pioche",         "Outils", min_tier=2),
    ResourceKind("TOOL_AXE",     "Hache de recolte", "Outils", min_tier=2),
    ResourceKind("TOOL_KNIFE",   "Couteau",        "Outils", min_tier=2),
    ResourceKind("TOOL_ROD",     "Canne a peche",  "Outils", min_tier=2),
]

# ----------------------------------------------------------------------------
# Categorie : Montures (T3-T8)
# ----------------------------------------------------------------------------
MOUNTS: list[ResourceKind] = [
    ResourceKind("MOUNT_HORSE",      "Cheval",            "Montures", min_tier=3, enchantable=False),
    ResourceKind("MOUNT_OX",         "Boeuf de trait",    "Montures", min_tier=3, enchantable=False),
    ResourceKind("MOUNT_DIREWOLF",   "Loup-garou",        "Montures", min_tier=5, enchantable=False),
    ResourceKind("MOUNT_STAG",       "Cerf",              "Montures", min_tier=5, enchantable=False),
    ResourceKind("MOUNT_SWIFTCLAW",  "Griffe rapide",     "Montures", min_tier=5, enchantable=False),
    ResourceKind("MOUNT_ARMORED",    "Cheval lourd",      "Montures", min_tier=7, enchantable=False),
]


# Catalogue aplati pour lookup rapide par key
ALL_KINDS: list[ResourceKind] = (
    RAW_MATERIALS
    + REFINED_MATERIALS
    + WEAPONS
    + OFFHANDS
    + ARMORS
    + ACCESSORIES
    + CONSUMABLES
    + TOOLS
    + MOUNTS
)

KIND_BY_KEY: dict[str, ResourceKind] = {k.key: k for k in ALL_KINDS}


def categories() -> list[str]:
    """Liste ordonnee des categories, dans l'ordre d'affichage souhaite."""
    seen: list[str] = []
    for k in ALL_KINDS:
        if k.category not in seen:
            seen.append(k.category)
    return seen


def kinds_in_category(category: str) -> list[ResourceKind]:
    return [k for k in ALL_KINDS if k.category == category]


def format_item(key: str, tier: int, ench: int) -> str:
    """Formatte un item en texte : 'Bois T4.3', 'Potion T5'."""
    kind = KIND_BY_KEY.get(key)
    name = kind.name if kind else key
    if kind and not kind.enchantable:
        return f"{name} T{tier}"
    return f"{name} T{tier}.{ench}"


def tiers_for(kind: ResourceKind) -> list[int]:
    return list(range(kind.min_tier, kind.max_tier + 1))


def enchants_for(kind: ResourceKind) -> list[int]:
    return [0] if not kind.enchantable else [0, 1, 2, 3, 4]
