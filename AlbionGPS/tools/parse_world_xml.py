"""Parse the decrypted world.xml (from Albion's world.bin) and produce
data/zones.json with authoritative cluster colors.

Walkable overworld types we keep:
    SAFEAREA, STARTAREA, STARTINGCITY
    OPENPVP_YELLOW, OPENPVP_RED
    OPENPVP_BLACK_1..6
    PLAYERCITY_SAFEAREA_01, PLAYERCITY_SAFEAREA_02
    PLAYERCITY_BLACK_ROYAL (Caerleon)
    PLAYERCITY_BLACK, PLAYERCITY_BLACK_PORTALCITY_* (outlands player cities)

Anything else (arenas, tunnels, islands, dungeons, debug...) is dropped.
"""
from __future__ import annotations
import re, json, sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
XML_PATH = ROOT / "data" / "world.xml"
OUT_PATH = ROOT / "data" / "zones.json"

# type -> color
TYPE_COLOR = {
    "SAFEAREA": "blue",
    "STARTAREA": "blue",
    "STARTINGCITY": "blue",
    "PLAYERCITY_SAFEAREA_01": "blue",
    "PLAYERCITY_SAFEAREA_02": "blue",
    "PLAYERCITY_SAFEAREA_NOFURNITURE": "blue",
    "OPENPVP_YELLOW": "yellow",
    "OPENPVP_RED": "red",
    "PLAYERCITY_BLACK_ROYAL": "red",  # Caerleon
    "PLAYERCITY_BLACK_ROYAL_NOFURNITURE": "red",
    # Outlands
    "OPENPVP_BLACK_1": "black",
    "OPENPVP_BLACK_2": "black",
    "OPENPVP_BLACK_3": "black",
    "OPENPVP_BLACK_4": "black",
    "OPENPVP_BLACK_5": "black",
    "OPENPVP_BLACK_6": "black",
    "PLAYERCITY_BLACK": "black",
    "PLAYERCITY_BLACK_NOFURNITURE": "black",
    "PLAYERCITY_BLACK_REST": "black",
    "PLAYERCITY_BLACK_PORTALCITY_NOFURNITURE": "black",
    "PLAYERCITY_BLACK_SMUGGLERSDEN": "black",
}


def main() -> None:
    tree = ET.parse(XML_PATH)
    root = tree.getroot()
    clusters_el = root.find("clusters")
    assert clusters_el is not None

    # First pass: collect every cluster by id AND by displayname (display may collide)
    by_id: dict[str, dict] = {}
    kept: dict[str, dict] = {}  # display -> record (walkable only)

    for c in clusters_el.findall("cluster"):
        cid = c.attrib.get("id", "")
        ctype = c.attrib.get("type", "")
        display = c.attrib.get("displayname", cid)
        pos_attr = c.attrib.get("worldmapposition", "0 0")
        try:
            x, y = (float(v) for v in pos_attr.split())
        except Exception:
            x, y = 0.0, 0.0

        # Flip Y so north is positive (matches albiononline2d.com convention)
        pos = [x, -y]
        by_id[cid] = {
            "cid": cid,
            "display": display,
            "type": ctype,
            "pos": pos,
            "el": c,
        }

    # Second pass: keep only walkable + build name-based record.
    # Name collisions: we keep the first walkable variant (cities usually
    # have unique display, yellow zones too).
    for rec in by_id.values():
        color = TYPE_COLOR.get(rec["type"])
        if color is None:
            continue
        name = rec["display"]
        if name in kept:
            # prefer lower-priority color to avoid accidentally swapping city
            # types with a "nofurniture" duplicate that has a different type
            continue
        kept[name] = {
            "color": color,
            "tier": 0,
            "biome": "",
            "pos": rec["pos"],
            "type_raw": rec["type"],
            "cid": rec["cid"],
        }

    # Build connections from <exits>
    # targetid can be:
    #   - a plain cluster id  "3014-HALL-01"
    #   - "<guid>@<clusterid>"   -> we take the part after '@'
    connections: set[tuple[str, str]] = set()
    cid_to_display = {r["cid"]: r["display"] for r in by_id.values()}

    for rec in by_id.values():
        if rec["display"] not in kept:
            continue
        src = rec["display"]
        exits = rec["el"].find("exits")
        if exits is None:
            continue
        for ex in exits.findall("exit"):
            target = ex.attrib.get("targetid", "")
            if not target:
                continue
            if "@" in target:
                target_cid = target.split("@", 1)[1]
            else:
                target_cid = target
            dst = cid_to_display.get(target_cid)
            if dst is None:
                continue
            if dst not in kept:
                continue
            if dst == src:
                continue
            a, b = sorted((src, dst))
            connections.add((a, b))

    out = {
        "zones": kept,
        "connections": sorted(list(connections)),
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Stats
    from collections import Counter
    colors = Counter(z["color"] for z in kept.values())
    print(f"wrote {OUT_PATH}")
    print(f"  zones={len(kept)}  connections={len(connections)}")
    print(f"  colors={dict(colors)}")


if __name__ == "__main__":
    main()
