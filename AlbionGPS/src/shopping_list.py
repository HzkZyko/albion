"""
Modele + persistance de la liste de course.

Une ShoppingList contient une sequence d'entrees ShoppingEntry, chacune
identifiee par (kind_key, tier, ench) - on ne veut pas deux fois le meme
item dans la liste, si l'utilisateur ajoute a nouveau "Bois T4.3" on
augmente simplement la quantite cible.

Les quantites :
- target  : ce que l'utilisateur veut acheter au total
- current : ce qu'il a deja (incremente manuellement via +/- ou a terme
            automatiquement par le sniffer Photon quand on aura reverse
            l'event de confirmation d'achat au marche)

Persistance : JSON plat dans data/shopping_list.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class ShoppingEntry:
    kind: str      # cle de ResourceKind (ex. "WOOD")
    tier: int
    ench: int
    target: int
    current: int = 0
    # Prix unitaire saisi manuellement par l'utilisateur (silver/unite).
    # Sert a calculer le budget de la ligne et le total global.
    unit_cost: int = 0
    # Legacy : silver total depense (auto-detection Photon). On garde le
    # champ pour compat de l'ancien save file, mais il n'est plus utilise
    # activement - le total ligne se calcule desormais a partir de
    # unit_cost * target.
    total_cost: int = 0

    @property
    def ident(self) -> tuple[str, int, int]:
        return (self.kind, self.tier, self.ench)

    @property
    def done(self) -> bool:
        return self.current >= self.target

    @property
    def progress(self) -> float:
        if self.target <= 0:
            return 0.0
        return min(1.0, self.current / self.target)

    @property
    def line_budget(self) -> int:
        """Budget total pour acheter toute la target au prix unitaire courant."""
        return int(self.unit_cost) * int(self.target)

    @property
    def line_spent(self) -> int:
        """Silver theoriquement depense pour ce qui a deja ete achete."""
        return int(self.unit_cost) * int(self.current)

    @property
    def line_remaining(self) -> int:
        """Silver restant a depenser pour finir la ligne."""
        return max(0, self.line_budget - self.line_spent)


@dataclass
class ShoppingList:
    entries: list[ShoppingEntry] = field(default_factory=list)

    # ----------------------------------------------------------- mutations

    def add(self, kind: str, tier: int, ench: int, qty: int) -> ShoppingEntry:
        """Ajoute qty unites d'un item. Si l'item existe deja, incremente la
        cible existante plutot que de creer un doublon."""
        for e in self.entries:
            if e.ident == (kind, tier, ench):
                e.target += qty
                return e
        entry = ShoppingEntry(kind=kind, tier=tier, ench=ench, target=qty, current=0)
        self.entries.append(entry)
        return entry

    def remove(self, ident: tuple[str, int, int]) -> None:
        self.entries = [e for e in self.entries if e.ident != ident]

    def set_current(self, ident: tuple[str, int, int], current: int) -> None:
        for e in self.entries:
            if e.ident == ident:
                e.current = max(0, min(current, e.target))
                return

    def bump_current(self, ident: tuple[str, int, int], delta: int) -> None:
        for e in self.entries:
            if e.ident == ident:
                e.current = max(0, min(e.current + delta, e.target))
                return

    def set_unit_cost(self, ident: tuple[str, int, int], cost: int) -> None:
        """Met a jour le prix unitaire saisi par l'utilisateur."""
        cost = max(0, int(cost))
        for e in self.entries:
            if e.ident == ident:
                e.unit_cost = cost
                return

    def toggle_done(self, ident: tuple[str, int, int]) -> None:
        """Bascule entre 'vide' (current=0) et 'fini' (current=target)."""
        for e in self.entries:
            if e.ident == ident:
                e.current = 0 if e.done else e.target
                return

    def clear_done(self) -> int:
        before = len(self.entries)
        self.entries = [e for e in self.entries if not e.done]
        return before - len(self.entries)

    def clear_all(self) -> None:
        self.entries = []

    # ----------------------------------------------------------- market feed

    def record_purchase(
        self,
        kind: str,
        tier: int,
        ench: int,
        qty: int,
        total_silver: int,
    ) -> Optional[ShoppingEntry]:
        """Enregistre un achat detecte par le sniffer Photon.

        Auto-match STRICT : on n'incremente que si une entree existante
        correspond exactement au triplet (kind, tier, ench). Les achats
        sans match (autres items, items non catalogues) sont ignores par
        cette methode - ils peuvent etre trackes separement via le journal.

        Retourne l'entree mise a jour, ou None si aucun match.

        On n'etouffe pas non plus les depassements : si le joueur achete
        plus que sa cible, on clampe current=target mais on credite tout le
        silver depense, de facon a garder une trace reelle du cout.
        """
        for e in self.entries:
            if e.ident == (kind, tier, ench):
                e.current = min(e.current + qty, e.target)
                e.total_cost += int(total_silver)
                return e
        return None

    def total_spent(self) -> int:
        """Silver total depense (somme de unit_cost * current sur toutes les lignes)."""
        return sum(e.line_spent for e in self.entries)

    def total_budget(self) -> int:
        """Budget total estime pour finir toute la liste (unit_cost * target)."""
        return sum(e.line_budget for e in self.entries)

    def total_remaining(self) -> int:
        """Silver restant a depenser pour finir toute la liste."""
        return sum(e.line_remaining for e in self.entries)

    # ----------------------------------------------------------- stats

    def stats(self) -> tuple[int, int, int]:
        """(nb entries total, nb terminees, total unites cibles)."""
        total = len(self.entries)
        done = sum(1 for e in self.entries if e.done)
        units = sum(e.target for e in self.entries)
        return total, done, units

    # ----------------------------------------------------------- persistence

    @classmethod
    def load(cls, path: Path) -> "ShoppingList":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = [
                ShoppingEntry(
                    kind=d.get("kind", ""),
                    tier=int(d.get("tier", 1)),
                    ench=int(d.get("ench", 0)),
                    target=int(d.get("target", 0)),
                    current=int(d.get("current", 0)),
                    unit_cost=int(d.get("unit_cost", 0)),
                    total_cost=int(d.get("total_cost", 0)),
                )
                for d in data
                if d.get("kind")
            ]
            return cls(entries=entries)
        except (OSError, ValueError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump([asdict(e) for e in self.entries], f, indent=2)
        except OSError:
            pass
