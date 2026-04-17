"""
Convertit le fichier zoneData.json (format SugarF0x/albion-navigator) en
un zones.json utilisable par Albion GPS.

Le format source :
    [
      {
        "id": 0,
        "displayName": "Thetford",
        "position": [-19.82, 187.29],
        "type": 1,
        "connections": [6, 5, 7, 9, 27],
        "components": [],
        "layer": 0
      },
      ...
    ]

On ne garde que layer == 0 (monde principal : royal + outlands). Les
Avalonian Roads (layer > 0) seront ajoutees plus tard si besoin, car
leur topologie change dynamiquement.

Mapping des types vers la couleur de securite Albion :
  0 -> blue    (Crosses, hubs surs)
  1 -> blue    (Villes, rests, portals) sauf Caerleon -> red
  2 -> yellow
  3 -> red
  4 -> red     (zones rouges profondes T7/T8)
  5 -> black   (Outlands)
  6 -> skip    (Avalonian Roads, dynamiques)

Lancement :
    python tools/convert_zonedata.py chemin/vers/zoneData.json

Ecrit data/zones.json en sortie.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


TYPE_TO_COLOR = {
    0: "blue",    # Crosses (5 hubs surs)
    1: "blue",    # Villes (override Caerleon plus bas)
    2: "blue",    # Zones directement voisines des villes/crosses : safe
    3: "yellow",  # Zones PvP avec knockdown uniquement
    4: "red",     # Zones PvP full loot du continent royal
    5: "black",   # Outlands
}

# Overrides par nom : certaines "villes" (type 1) sont en fait des zones
# rouges ou specifiques. On les force ici.
NAME_COLOR_OVERRIDES = {
    "Caerleon": "red",
}

# On garde uniquement le monde principal, pas les Avalonian Roads.
ALLOWED_LAYERS = {0}

# Un biome decoulant grossierement de la position sur la carte. C'est
# optionnel mais pratique pour afficher une pastille biome cote UI.
def infer_biome(x: float, y: float) -> str:
    # Heuristique tres simple basee sur la forme du monde d'Albion.
    # Royal continent : centre = Caerleon (~0, 280), rayons vers les 5
    # villes. On tolere une erreur : c'est juste un label visuel.
    if y > 200 and -120 < x < 120:
        return "royal"
    if y > 0:
        return "roads"
    return "outlands"


def convert(source_path: Path, target_path: Path) -> None:
    data = json.loads(source_path.read_text(encoding="utf-8"))

    by_id: dict[int, dict] = {z["id"]: z for z in data}

    # Premier passage : selection des zones a garder.
    kept: dict[int, dict] = {}
    for z in data:
        if z.get("layer", 0) not in ALLOWED_LAYERS:
            continue
        t = z.get("type")
        if t not in TYPE_TO_COLOR:
            continue
        kept[z["id"]] = z

    # Deuxieme passage : construction des zones + connexions.
    zones_out: dict[str, dict] = {}
    connections_out: list[list[str]] = []
    name_by_id: dict[int, str] = {}
    seen_names: set[str] = set()

    for zid, z in kept.items():
        name = z["displayName"].strip()
        # En cas de doublon de nom (rare mais possible), on suffixe par id.
        if name in seen_names:
            name = f"{name} #{zid}"
        seen_names.add(name)
        name_by_id[zid] = name

        color = NAME_COLOR_OVERRIDES.get(name) or TYPE_TO_COLOR[z["type"]]
        pos = z.get("position") or [0.0, 0.0]
        x, y = float(pos[0]), float(pos[1])

        zones_out[name] = {
            "color": color,
            "tier": 0,  # le zoneData ne contient pas le tier exact
            "biome": infer_biome(x, y),
            "pos": [round(x, 2), round(y, 2)],
            "source_id": zid,
            "source_type": z["type"],
        }

    # Connexions : on dedoublonne en triant les paires.
    seen_pairs: set[tuple[str, str]] = set()
    for zid, z in kept.items():
        a = name_by_id.get(zid)
        if a is None:
            continue
        for other_id in z.get("connections") or ():
            if other_id not in kept:
                continue
            b = name_by_id.get(other_id)
            if b is None or b == a:
                continue
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            connections_out.append([pair[0], pair[1]])

    out = {
        "_comment": (
            "Genere par tools/convert_zonedata.py depuis zoneData.json. "
            "Layer 0 uniquement (monde principal). Positions en "
            "coordonnees monde, utilisees pour calculer les directions "
            "cardinales entre zones."
        ),
        "zones": zones_out,
        "connections": sorted(connections_out),
    }

    target_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Statistiques finales
    from collections import Counter
    col = Counter(z["color"] for z in zones_out.values())
    print(f"Zones ecrites : {len(zones_out)}")
    print(f"Connexions    : {len(connections_out)}")
    print(f"Couleurs      : {dict(col)}")
    print(f"Fichier       : {target_path}")


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    default_source = here / "data" / "zoneData_raw.json"
    default_target = here / "data" / "zones.json"

    if len(sys.argv) >= 2:
        source = Path(sys.argv[1])
    else:
        source = default_source
    target = Path(sys.argv[2]) if len(sys.argv) >= 3 else default_target

    if not source.exists():
        print(f"Source introuvable : {source}", file=sys.stderr)
        print(
            "Usage : python tools/convert_zonedata.py <zoneData.json> [zones.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    convert(source, target)


if __name__ == "__main__":
    main()
