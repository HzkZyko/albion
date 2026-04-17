"""
Index des clusters d'Albion charge depuis data/world.xml.

Construit une table qui mappe tous les identifiants plausibles qu'un paquet
Photon pourrait contenir (id court, nom de fichier, nom de fichier sans
extension, display name) vers le nom d'affichage de la zone.

Cet index est utilise par le sniffer Photon pour reconnaitre la zone
courante meme quand le protocole envoie un identifiant interne plutot
qu'un nom lisible par l'humain.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ClusterInfo:
    id: str
    display_name: str
    file: Optional[str]
    type: Optional[str]


class WorldIndex:
    def __init__(self) -> None:
        # Cle = identifiant potentiel (lowercase), Valeur = display_name
        self._by_string: dict[str, str] = {}
        # Cle = cluster id interprete comme int, Valeur = display_name
        self._by_int: dict[int, str] = {}
        self._clusters: list[ClusterInfo] = []

    # ------------------------------------------------------------------ load

    @classmethod
    def from_xml(cls, path: str | Path) -> "WorldIndex":
        idx = cls()
        tree = ET.parse(str(path))
        root = tree.getroot()
        # IDs entiers en-dessous de ce seuil sont consideres trop ambigus
        # pour etre matchables de maniere fiable : 0, 1, 2... sont omni-
        # presents dans les paquets Photon (defaults, flags, compteurs).
        # Cluster 0 = Thetford, 4-8 = sous-zones de Thetford / Camlann.
        # Ces clusters restent detectables via leur display_name ou leur
        # filename complet. Seuil choisi pour couvrir toutes les vraies
        # zones du jeu (la plus petite ID utile est ~201 = Sleetwater).
        MIN_SAFE_INT_ID = 100

        for c in root.iter("cluster"):
            cid = c.get("id") or ""
            display = c.get("displayname") or cid
            file = c.get("file")
            typ = c.get("type")
            if not cid:
                continue
            info = ClusterInfo(id=cid, display_name=display, file=file, type=typ)
            idx._clusters.append(info)

            # String keys : on enregistre cid, display, filename, etc.
            # MAIS on skip les prefixes numeriques <= 4 chars (0000, 0004,
            # etc.) qui vont matcher des chaines courtes hasardeuses.
            def _is_short_numeric(s: str) -> bool:
                return bool(s) and s.isdigit() and len(s) <= 4

            if not _is_short_numeric(cid):
                idx._register(cid, display)
            if display and not _is_short_numeric(display):
                idx._register(display, display)
            if file:
                idx._register(file, display)
                if file.endswith(".cluster.xml"):
                    idx._register(file[: -len(".cluster.xml")], display)
                base = file.split("_", 1)[0]
                if base and base != cid and not _is_short_numeric(base):
                    idx._register(base, display)

            # Int si l'id est un nombre entier pur, mais on exclut les
            # valeurs trop petites (sources massives de faux positifs).
            try:
                as_int = int(cid)
                if as_int >= MIN_SAFE_INT_ID and as_int not in idx._by_int:
                    idx._by_int[as_int] = display
            except ValueError:
                pass
        return idx

    def _register(self, key: str, display: str) -> None:
        if not key:
            return
        k = key.strip().lower()
        if not k:
            return
        # On ne remplace pas une entree existante : priorite au 1er match
        if k not in self._by_string:
            self._by_string[k] = display

    # -------------------------------------------------------------- lookups

    def lookup_string(self, s: str) -> Optional[str]:
        if not s:
            return None
        return self._by_string.get(s.strip().lower())

    def lookup_int(self, n: int) -> Optional[str]:
        return self._by_int.get(n)

    def __len__(self) -> int:
        return len(self._clusters)

    @property
    def all_display_names(self) -> set[str]:
        return {c.display_name for c in self._clusters if c.display_name}
