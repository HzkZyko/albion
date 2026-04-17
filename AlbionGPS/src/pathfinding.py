"""
Albion GPS - Moteur de pathfinding.

Charge le graphe des zones depuis data/zones.json et calcule le plus court
chemin entre deux zones, avec option pour eviter les zones non sures.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


SAFE_COLORS = {"blue", "yellow"}
ALL_COLORS = {"blue", "yellow", "red", "black"}


@dataclass(frozen=True)
class Zone:
    name: str
    color: str
    tier: int
    biome: str
    pos: Optional[tuple[float, float]] = None

    @property
    def is_safe(self) -> bool:
        return self.color in SAFE_COLORS


def compute_cardinal(
    from_pos: Optional[tuple[float, float]],
    to_pos: Optional[tuple[float, float]],
) -> Optional[str]:
    """Retourne une direction 8-cardinal (N/NE/E/SE/S/SW/W/NW) entre deux
    points 2D. Convention du dataset zoneData : x augmente vers l'est,
    y augmente vers le SUD (coord image, y=0 en haut). Verifie : Bridgewatch
    (cite SW) y=370 > Caerleon (centre) y=288 > Fort Sterling (N) y=208."""
    if from_pos is None or to_pos is None:
        return None
    dx = to_pos[0] - from_pos[0]
    # On inverse dy pour passer dans un repere mathematique classique
    # (y positif = nord), ce qui permet d'utiliser atan2 directement.
    dy = -(to_pos[1] - from_pos[1])
    if dx == 0 and dy == 0:
        return None
    # atan2(dy, dx) : 0 = E, 90 = N (apres inversion de dy).
    angle = math.degrees(math.atan2(dy, dx))
    angle = (angle + 360.0) % 360.0
    sectors = [
        (22.5,  "E"),
        (67.5,  "NE"),
        (112.5, "N"),
        (157.5, "NW"),
        (202.5, "W"),
        (247.5, "SW"),
        (292.5, "S"),
        (337.5, "SE"),
        (360.0, "E"),
    ]
    for limit, label in sectors:
        if angle < limit:
            return label
    return "E"


@dataclass
class Route:
    path: list[str]
    total_hops: int
    crossed_colors: list[str]

    @property
    def is_fully_safe(self) -> bool:
        return all(c in SAFE_COLORS for c in self.crossed_colors)


class WorldGraph:
    """Graphe non-oriente des zones d'Albion."""

    def __init__(self, zones: dict[str, Zone], adjacency: dict[str, set[str]]):
        self.zones = zones
        self.adjacency = adjacency

    # ------------------------------------------------------------------ load

    @classmethod
    def from_json(cls, path: str | Path) -> "WorldGraph":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        zones: dict[str, Zone] = {}
        for name, meta in data["zones"].items():
            pos_raw = meta.get("pos")
            pos = None
            if isinstance(pos_raw, (list, tuple)) and len(pos_raw) >= 2:
                pos = (float(pos_raw[0]), float(pos_raw[1]))
            zones[name] = Zone(
                name=name,
                color=meta.get("color", "blue"),
                tier=int(meta.get("tier", 0) or 0),
                biome=meta.get("biome", "unknown"),
                pos=pos,
            )

        adjacency: dict[str, set[str]] = {n: set() for n in zones}
        for a, b in data["connections"]:
            if a not in zones or b not in zones:
                # On ignore silencieusement les references inconnues, pour
                # rester tolerant aux modifications manuelles du JSON.
                continue
            adjacency[a].add(b)
            adjacency[b].add(a)
        return cls(zones, adjacency)

    # --------------------------------------------------------------- queries

    def zone_names(self) -> list[str]:
        return sorted(self.zones)

    def neighbors(self, zone: str, allowed_colors: Iterable[str]) -> Iterable[str]:
        allowed = set(allowed_colors)
        for n in self.adjacency.get(zone, ()):  # pragma: no branch
            if self.zones[n].color in allowed:
                yield n

    # ------------------------------------------------------------ pathfinding

    def find_route(
        self,
        start: str,
        goal: str,
        safe_only: bool = False,
    ) -> Route | None:
        """Dijkstra simple. Le poids d'une zone est 1, sauf zones rouges/noires
        qui valent un peu plus pour favoriser les detours surs quand c'est
        possible meme sans le filtre safe_only."""

        if start not in self.zones or goal not in self.zones:
            return None
        if start == goal:
            return Route(path=[start], total_hops=0, crossed_colors=[self.zones[start].color])

        allowed = SAFE_COLORS if safe_only else ALL_COLORS

        # Verification de base : depart et arrivee doivent etre autorises.
        # Si l'utilisateur demande une route safe vers Caerleon, c'est
        # impossible : on retourne None et l'UI affichera un message clair.
        if self.zones[start].color not in allowed:
            return None
        if self.zones[goal].color not in allowed:
            return None

        weights = {"blue": 1.0, "yellow": 1.05, "red": 1.5, "black": 2.0}

        dist: dict[str, float] = {start: 0.0}
        prev: dict[str, str | None] = {start: None}
        heap: list[tuple[float, str]] = [(0.0, start)]

        while heap:
            d, current = heapq.heappop(heap)
            if current == goal:
                break
            if d > dist.get(current, float("inf")):
                continue
            for nb in self.neighbors(current, allowed):
                step = weights.get(self.zones[nb].color, 1.0)
                nd = d + step
                if nd < dist.get(nb, float("inf")):
                    dist[nb] = nd
                    prev[nb] = current
                    heapq.heappush(heap, (nd, nb))

        if goal not in prev:
            return None

        # Reconstruction du chemin.
        path: list[str] = []
        node: str | None = goal
        while node is not None:
            path.append(node)
            node = prev[node]
        path.reverse()

        return Route(
            path=path,
            total_hops=len(path) - 1,
            crossed_colors=[self.zones[n].color for n in path],
        )
