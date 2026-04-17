"""
Analyse du dump photon_capture.log genere par src/photon_sniffer.py
ou tools/capture_photon.py (format legerement different, on gere les deux).

Objectif : identifier, pour chaque couple (kind, code, param_key) de message
Photon, quelles zones ont ete matchees et combien de fois. Le couple qui
contient le cluster *du joueur* (et pas juste des references a des zones
voisines ou de marketplace) est celui dont :
 - une seule zone domine a un instant T,
 - et cette zone change quand le joueur se deplace.

Usage :
    .venv\\Scripts\\python.exe tools\\analyze_photon_log.py
    .venv\\Scripts\\python.exe tools\\analyze_photon_log.py --zone Martlock

Avec --zone, on filtre : n'affiche que les tuples ou la zone declaree a
ete matchee. Ca permet d'identifier rapidement le bon parametre quand on
sait qu'on est reste dans une zone donnee pendant la capture.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.photon_proto import PhotonMessage, parse_photon_packet  # noqa: E402
from src.world_index import WorldIndex  # noqa: E402


DUMP_PATH = ROOT / "data" / "photon_capture.log"
WORLD_XML = ROOT / "data" / "world.xml"


def iter_packets_sniffer_format(data: bytes):
    """Format ecrit par src/photon_sniffer.py : header = >dI (ts + len)."""
    pos = 0
    n = len(data)
    while pos + 12 <= n:
        ts, length = struct.unpack_from(">dI", data, pos)
        pos += 12
        if length <= 0 or pos + length > n:
            break
        yield ts, data[pos : pos + length]
        pos += length


def iter_packets_calibrator_format(data: bytes):
    """Format ecrit par tools/capture_photon.py : header = >dB15s15sI."""
    pos = 0
    n = len(data)
    header_size = struct.calcsize(">dB15s15sI")
    while pos + header_size <= n:
        ts, _direction, _src, _dst, length = struct.unpack_from(">dB15s15sI", data, pos)
        pos += header_size
        if length <= 0 or pos + length > n:
            break
        yield ts, data[pos : pos + length]
        pos += length


def detect_format(data: bytes) -> str:
    """Heuristique pour choisir entre les deux formats de header."""
    if len(data) < 12:
        return "sniffer"
    # Header calibrator : 44 bytes, payload suit. Si on lit 44 bytes de la
    # bonne maniere, le length extrait doit etre plausible (< 2048).
    header_size = struct.calcsize(">dB15s15sI")
    if len(data) >= header_size:
        try:
            _ts, _d, _s, _ds, length = struct.unpack_from(">dB15s15sI", data, 0)
            if 1 <= length <= 2048 and header_size + length <= len(data):
                # Verifie aussi le premier octet du payload : Photon cmd_count
                # n'est jamais une valeur aberrante.
                return "calibrator"
        except struct.error:
            pass
    return "sniffer"


def match_value(index: WorldIndex, value):
    """Retourne le display_name si la valeur matche un cluster, sinon None.
    Ne recurse PAS : on veut la clef EXACTE qui a matche."""
    if isinstance(value, str):
        return index.lookup_string(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return index.lookup_int(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            s = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        if all(32 <= ord(c) < 127 for c in s):
            return index.lookup_string(s)
    return None


def match_value_recursive(index: WorldIndex, value, path: str = ""):
    """Version recursive : yield (sub_path, zone) pour chaque match trouve."""
    direct = match_value(index, value)
    if direct is not None:
        yield path, direct
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from match_value_recursive(index, v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from match_value_recursive(index, v, f"{path}.{k}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", help="Filtre : ne montre que les tuples ayant matche cette zone")
    ap.add_argument("--top", type=int, default=30, help="Nb de tuples a afficher")
    ap.add_argument("--path", type=Path, default=DUMP_PATH, help="Chemin du dump")
    args = ap.parse_args()

    dump_path: Path = args.path
    if not dump_path.exists():
        print(f"[ERREUR] Pas de dump trouve a {dump_path}")
        sys.exit(1)
    data = dump_path.read_bytes()
    if not data:
        print(f"[ERREUR] Le dump {dump_path} est vide (0 octets).")
        print("         Lance l'application ou tools/capture_photon.py et rejoue")
        print("         quelques minutes dans Albion pour remplir le log.")
        sys.exit(1)

    fmt = detect_format(data)
    print(f"[INFO] Dump : {dump_path} ({len(data)} octets, format={fmt})")

    if fmt == "calibrator":
        packets = list(iter_packets_calibrator_format(data))
    else:
        packets = list(iter_packets_sniffer_format(data))
    print(f"[INFO] {len(packets)} paquets Photon extraits du dump")

    if not WORLD_XML.exists():
        print(f"[ERREUR] world.xml introuvable ({WORLD_XML})")
        sys.exit(1)
    index = WorldIndex.from_xml(WORLD_XML)
    print(f"[INFO] WorldIndex charge : {len(index)} clusters")
    print()

    # Cle = (kind, code, param_key_path), Valeur = Counter{zone: count}
    tuple_stats: dict[tuple, Counter] = defaultdict(Counter)
    # Timeline : pour chaque tuple, liste de (ts, zone) afin de voir si ca bouge.
    tuple_timeline: dict[tuple, list] = defaultdict(list)

    total_messages = 0
    total_matches = 0
    for ts, payload in packets:
        try:
            messages = parse_photon_packet(payload)
        except Exception:
            continue
        total_messages += len(messages)
        for msg in messages:
            for pkey, pvalue in msg.params.items():
                for sub_path, zone in match_value_recursive(index, pvalue, ""):
                    tuple_key = (msg.kind, msg.code, f"{pkey}{sub_path}")
                    tuple_stats[tuple_key][zone] += 1
                    if len(tuple_timeline[tuple_key]) < 50:
                        tuple_timeline[tuple_key].append((ts, zone))
                    total_matches += 1

    print(f"[INFO] {total_messages} messages Photon decodes, {total_matches} matchs de cluster")
    print()

    if not tuple_stats:
        print("[WARN] Aucun match trouve dans ce dump.")
        print("       Verifie que tu as bien joue dans au moins une zone connue.")
        return

    # Si on a un filtre --zone, on ne garde que les tuples qui ont matche cette zone
    if args.zone:
        zone_lower = args.zone.strip().lower()
        filtered = {
            k: v for k, v in tuple_stats.items()
            if any(z.lower() == zone_lower for z in v)
        }
        if not filtered:
            print(f"[WARN] Aucun tuple n'a matche '{args.zone}' dans ce dump.")
            print("       Zones reellement matchees :")
            all_zones = Counter()
            for v in tuple_stats.values():
                for z, c in v.items():
                    all_zones[z] += c
            for z, c in all_zones.most_common(20):
                print(f"       - {z}: {c}")
            return
        tuple_stats = filtered

    # Classement par "purete" : tuple qui ne matche QU'UNE zone (ou une zone a >90%)
    # et qui a au moins N matchs.
    print("=" * 72)
    print(f"TOP {args.top} TUPLES (kind, code, param_key) -> distribution de zones")
    print("=" * 72)
    ranked = sorted(
        tuple_stats.items(),
        key=lambda kv: (-sum(kv[1].values()), -max(kv[1].values()) / max(sum(kv[1].values()), 1)),
    )
    for tuple_key, zones in ranked[: args.top]:
        kind, code, key_path = tuple_key
        total = sum(zones.values())
        top_zone, top_count = zones.most_common(1)[0]
        purity = top_count / total * 100
        distinct = len(zones)
        tag = "[PURE]" if distinct == 1 else f"[{distinct} zones]"
        print(f"{tag:>10}  {kind:11s} code={code:3d}  key={key_path:20s}  "
              f"n={total:5d}  top={top_zone!r} ({purity:.0f}%)")
        if distinct > 1:
            # On montre la repartition pour debug
            for z, c in zones.most_common(5):
                print(f"              . {z}: {c}")
    print()
    print("=" * 72)
    print("Tuples 100% purs avec au moins 5 matchs (candidats pour la detection) :")
    print("=" * 72)
    for tuple_key, zones in ranked:
        total = sum(zones.values())
        if len(zones) == 1 and total >= 5:
            kind, code, key_path = tuple_key
            (zone, _),  = zones.most_common(1)
            print(f"  {kind:11s} code={code:3d}  key={key_path:20s}  "
                  f"n={total:5d}  -> {zone}")


if __name__ == "__main__":
    main()
