"""
Test minimal de capture Photon.

Lance EN ADMIN :
    .venv\Scripts\python.exe tools\minimal_sniffer.py

Traverse un portail dans le jeu. Apres 30 secondes, le script affiche
un rapport complet de ce qu'il a vu.
"""

import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.world_index import WorldIndex
from src.photon_proto import FragmentBuffer, parse_photon_packet

WORLD_XML = Path(__file__).resolve().parent.parent / "data" / "world.xml"
CAPTURE_SECONDS = 30
PHOTON_PORTS = (5055, 5056)

print("[*] Chargement WorldIndex...")
world_index = WorldIndex.from_xml(WORLD_XML)
print(f"[*] {len(world_index)} clusters charges.\n")

# Compteurs
stats = {
    "udp_pkts": 0,
    "tcp_pkts": 0,
    "udp_bytes": 0,
    "tcp_bytes": 0,
    "photon_msgs": 0,
    "events": 0,
    "op_requests": 0,
    "op_responses": 0,
    "raw_0x73_hits": 0,      # Nombre de bytes 0x73 trouves
    "raw_strings": 0,         # Strings valides apres filtrage
    "raw_zone_matches": 0,
    "zone_strings": [],       # Strings qui matchent des zones
    "all_strings": [],        # Toutes les strings trouvees
    "fragment_received": 0,
    "fragment_assembled": 0,
    "assembled_sigs": [],     # Premier byte de chaque fragment assemble
    "op_resp_details": [],    # Details des op_responses
    # Ports vus
    "port_counts": defaultdict(int),
    # Taille des paquets
    "pkt_sizes": [],
}

frag_buf = FragmentBuffer()


def raw_scan(payload):
    """Scan brut pour 0x73 string markers."""
    n = len(payload)
    i = 0
    while i < n - 3:
        if payload[i] == 0x73:
            stats["raw_0x73_hits"] += 1
            length = (payload[i + 1] << 8) | payload[i + 2]
            if 2 <= length <= 120 and i + 3 + length <= n:
                raw = payload[i + 3 : i + 3 + length]
                if all(32 <= b < 127 for b in raw):
                    try:
                        s = raw.decode("utf-8")
                        stats["raw_strings"] += 1
                        if len(stats["all_strings"]) < 50:
                            stats["all_strings"].append(s)
                        m = world_index.lookup_string(s)
                        if m:
                            stats["raw_zone_matches"] += 1
                            stats["zone_strings"].append(f"{s} -> {m}")
                        i += 3 + length
                        continue
                    except Exception:
                        pass
        i += 1


def brute_force_zone_scan(payload):
    """Cherche des noms de zone en brut dans le payload (pas juste 0x73)."""
    try:
        # Essaie de decoder tout le payload comme UTF-8 par morceaux
        for name in world_index.all_display_names:
            if len(name) < 4:
                continue
            name_bytes = name.encode("utf-8")
            if name_bytes in payload:
                stats["zone_strings"].append(f"BRUTE:{name}")
                return True
            # Aussi en lowercase
            if name_bytes.lower() in payload.lower():
                stats["zone_strings"].append(f"BRUTE-CI:{name}")
                return True
    except Exception:
        pass
    return False


def process_udp(payload, sport, dport):
    stats["udp_pkts"] += 1
    stats["udp_bytes"] += len(payload)
    stats["pkt_sizes"].append(len(payload))
    stats["port_counts"][(f"UDP", sport, dport)] += 1

    # Raw scan
    raw_scan(payload)

    # Brute force zone scan
    brute_force_zone_scan(payload)

    # Parse Photon
    try:
        frag_buf.last_assembled.clear()
        messages = parse_photon_packet(payload, frag_buf)
    except Exception:
        messages = []

    # Scan assembled fragments
    for asm in frag_buf.last_assembled:
        stats["fragment_assembled"] += 1
        sig = asm[0] if asm else 0
        mt = (asm[1] & 0x7F) if len(asm) > 1 else -1
        stats["assembled_sigs"].append(f"sig=0x{sig:02x} mt={mt} len={len(asm)}")
        raw_scan(asm)
        brute_force_zone_scan(asm)

    for msg in messages:
        stats["photon_msgs"] += 1
        if msg.kind == "event":
            stats["events"] += 1
        elif msg.kind == "op_request":
            stats["op_requests"] += 1
        elif msg.kind == "op_response":
            stats["op_responses"] += 1
            keys = sorted(msg.params.keys())
            val8 = msg.params.get(8, "NO-KEY-8")
            stats["op_resp_details"].append(
                f"op#{msg.code} keys={keys[:10]} p8={repr(val8)[:60]}"
            )
            # Essaie aussi de matcher la zone via les params
            for k, v in msg.params.items():
                if isinstance(v, str) and v.strip():
                    m = world_index.lookup_string(v)
                    if m:
                        stats["zone_strings"].append(f"PARAM:{k}={v}->{m}")


def process_tcp(payload, sport, dport):
    stats["tcp_pkts"] += 1
    stats["tcp_bytes"] += len(payload)
    stats["port_counts"][(f"TCP", sport, dport)] += 1

    # Raw scan
    raw_scan(payload)
    brute_force_zone_scan(payload)

    # Try Photon parse
    try:
        messages = parse_photon_packet(payload, None)
        for msg in messages:
            stats["photon_msgs"] += 1
            if msg.kind == "op_response":
                stats["op_responses"] += 1
                stats["op_resp_details"].append(
                    f"TCP op#{msg.code} keys={sorted(msg.params.keys())[:8]}"
                )
    except Exception:
        pass


def main():
    try:
        from scapy.all import sniff, IP, UDP, TCP, conf
    except ImportError:
        print("[ERREUR] scapy manquant")
        return

    try:
        conf.use_pcap = True
    except Exception:
        pass

    print(f"[*] Capture pendant {CAPTURE_SECONDS}s sur tous les ports Photon (UDP+TCP)")
    print("[*] TRAVERSE UN PORTAIL MAINTENANT !\n")

    def handler(pkt):
        if not pkt.haslayer(IP):
            return
        if pkt.haslayer(UDP):
            udp = pkt[UDP]
            if udp.sport in PHOTON_PORTS or udp.dport in PHOTON_PORTS:
                payload = bytes(udp.payload)
                if payload:
                    process_udp(payload, udp.sport, udp.dport)
        elif pkt.haslayer(TCP):
            tcp = pkt[TCP]
            if tcp.sport in PHOTON_PORTS or tcp.dport in PHOTON_PORTS:
                payload = bytes(tcp.payload)
                if payload and len(payload) > 0:
                    process_tcp(payload, tcp.sport, tcp.dport)

    # Capture avec filtre large
    bpf = "udp port 5055 or udp port 5056 or tcp port 5055 or tcp port 5056"
    try:
        sniff(filter=bpf, prn=handler, store=False, timeout=CAPTURE_SECONDS)
    except KeyboardInterrupt:
        pass

    # RAPPORT
    print(f"\n{'='*60}")
    print(f"  RAPPORT MINIMAL SNIFFER")
    print(f"{'='*60}\n")

    print(f"UDP : {stats['udp_pkts']} paquets, {stats['udp_bytes']//1024} KB")
    print(f"TCP : {stats['tcp_pkts']} paquets, {stats['tcp_bytes']//1024} KB")
    print()

    print(f"Photon messages : {stats['photon_msgs']}")
    print(f"  Events     : {stats['events']}")
    print(f"  Op_requests: {stats['op_requests']}")
    print(f"  Op_responses: {stats['op_responses']}")
    print()

    print(f"Fragments : recus={frag_buf.fragments_received}, assembles={stats['fragment_assembled']}")
    if stats["assembled_sigs"]:
        for s in stats["assembled_sigs"][:5]:
            print(f"  {s}")
    print()

    print(f"RAW scan :")
    print(f"  Bytes 0x73 trouves : {stats['raw_0x73_hits']}")
    print(f"  Strings valides    : {stats['raw_strings']}")
    print(f"  Zone matches       : {stats['raw_zone_matches']}")
    print()

    if stats["all_strings"]:
        print(f"Strings trouvees ({len(stats['all_strings'])}) :")
        for s in stats["all_strings"][:20]:
            print(f"  '{s}'")
    else:
        print("AUCUNE string trouvee dans le trafic !")
    print()

    if stats["zone_strings"]:
        print(f"ZONES DETECTEES :")
        for z in stats["zone_strings"][:10]:
            print(f"  {z}")
    else:
        print("AUCUNE zone detectee !")
    print()

    if stats["op_resp_details"]:
        print(f"Op_responses :")
        for d in stats["op_resp_details"][:10]:
            print(f"  {d}")
    print()

    # Taille des paquets
    if stats["pkt_sizes"]:
        sizes = sorted(stats["pkt_sizes"])
        print(f"Tailles paquets UDP : min={sizes[0]}, max={sizes[-1]}, "
              f"median={sizes[len(sizes)//2]}, total={len(sizes)}")
    print()

    # Ports vus
    print("Connexions (proto, sport, dport) :")
    sorted_ports = sorted(stats["port_counts"].items(), key=lambda x: -x[1])
    for (proto, sp, dp), count in sorted_ports[:15]:
        print(f"  {proto} {sp}->{dp} : {count} paquets")

    print(f"\n{'='*60}")
    print("Copie TOUT ce rapport et envoie-le dans le chat !")
    print(f"{'='*60}\n")
    input("Appuie sur Entree pour fermer...")


if __name__ == "__main__":
    main()
