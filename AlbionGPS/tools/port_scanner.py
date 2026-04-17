"""
Diagnostic : capture TOUT le trafic reseau pendant 15 secondes et identifie
quels (protocole, port) portent du trafic Photon ou des noms de zone Albion.

Usage : lancer EN ADMINISTRATEUR depuis le dossier AlbionGPS :
    .venv\Scripts\python.exe tools\port_scanner.py

Traverse un portail dans le jeu pendant que le script tourne.
Le script affiche un rapport a la fin.
"""

import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

# Ajoute le dossier parent au path pour importer les modules src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.world_index import WorldIndex

WORLD_XML = Path(__file__).resolve().parent.parent / "data" / "world.xml"
CAPTURE_SECONDS = 20

# Charge le WorldIndex pour reconnaitre les noms de zone
print("[*] Chargement du WorldIndex...")
world_index = WorldIndex.from_xml(WORLD_XML)
zone_names = world_index.all_display_names
# Prepare des bytes patterns pour recherche rapide
zone_bytes = {}
for name in zone_names:
    try:
        zone_bytes[name] = name.encode("utf-8").lower()
    except Exception:
        pass

print(f"[*] {len(zone_bytes)} zones indexees.")


def has_photon_signature(data: bytes) -> bool:
    """Verifie si le payload contient une signature Photon 0xF3."""
    return b"\xf3" in data


def find_zone_in_payload(data: bytes) -> list[str]:
    """Cherche des noms de zone en texte brut dans le payload."""
    lower = data.lower()
    found = []
    for name, pattern in zone_bytes.items():
        if pattern in lower:
            found.append(name)
    return found


def scan_photon_strings(data: bytes) -> list[str]:
    """Scan Photon string markers (0x73 + u16 len + ASCII)."""
    strings = []
    n = len(data)
    i = 0
    while i < n - 3:
        if data[i] != 0x73:
            i += 1
            continue
        length = (data[i + 1] << 8) | data[i + 2]
        if length < 2 or length > 120 or i + 3 + length > n:
            i += 1
            continue
        raw = data[i + 3 : i + 3 + length]
        if all(32 <= b < 127 for b in raw):
            try:
                strings.append(raw.decode("utf-8"))
            except Exception:
                pass
        i += 3 + max(length, 1)
    return strings


def main():
    try:
        from scapy.all import sniff, IP, UDP, TCP, conf  # type: ignore
    except ImportError:
        print("[ERREUR] scapy n'est pas installe.")
        print("Lance : .venv\\Scripts\\python.exe -m pip install scapy")
        return

    try:
        conf.use_pcap = True
    except Exception:
        pass

    print(f"\n[*] Capture de TOUT le trafic pendant {CAPTURE_SECONDS} secondes...")
    print("[*] TRAVERSE UN PORTAIL DANS LE JEU MAINTENANT !")
    print()

    # Stats par (proto, port)
    port_stats = defaultdict(lambda: {
        "count": 0,
        "bytes": 0,
        "photon_sig": 0,
        "zone_found": [],
        "photon_strings": 0,
        "sample_hex": None,
    })

    # Stats globales
    total_packets = 0
    all_zone_finds = []

    def handler(pkt):
        nonlocal total_packets
        if not pkt.haslayer(IP):
            return
        total_packets += 1

        if pkt.haslayer(UDP):
            proto = "UDP"
            layer = pkt[UDP]
        elif pkt.haslayer(TCP):
            proto = "TCP"
            layer = pkt[TCP]
        else:
            return

        sport = layer.sport
        dport = layer.dport
        payload = bytes(layer.payload)
        if not payload or len(payload) < 2:
            return

        # On indexe par le port "serveur" (le plus petit, ou celui connu)
        # Pour simplifier : on note les deux cotes
        for port in (sport, dport):
            key = (proto, port)
            st = port_stats[key]
            st["count"] += 1
            st["bytes"] += len(payload)

            if st["sample_hex"] is None:
                st["sample_hex"] = payload[:20].hex(" ")

            if has_photon_signature(payload):
                st["photon_sig"] += 1

            # Scan strings Photon
            strings = scan_photon_strings(payload)
            st["photon_strings"] += len(strings)

            # Cherche des noms de zone
            zones = find_zone_in_payload(payload)
            if zones:
                st["zone_found"].extend(zones)
                all_zone_finds.append((proto, port, zones))

    # Capture sans filtre BPF (tout le trafic)
    try:
        sniff(prn=handler, store=False, timeout=CAPTURE_SECONDS)
    except KeyboardInterrupt:
        pass

    # Rapport
    print(f"\n{'='*70}")
    print(f"  RAPPORT DE DIAGNOSTIC RESEAU")
    print(f"  {total_packets} paquets captures en {CAPTURE_SECONDS}s")
    print(f"{'='*70}\n")

    # Trie par nombre de paquets
    sorted_ports = sorted(port_stats.items(), key=lambda x: -x[1]["count"])

    # Affiche les ports avec du trafic significatif
    print("--- Ports avec trafic significatif (>5 paquets) ---\n")
    print(f"{'Proto':<6} {'Port':<8} {'Paquets':<10} {'KB':<8} {'Photon':<8} {'Strings':<8} {'Zones'}")
    print("-" * 70)

    for (proto, port), st in sorted_ports:
        if st["count"] < 5:
            continue
        zones_str = ", ".join(set(st["zone_found"])[:3]) if st["zone_found"] else ""
        print(
            f"{proto:<6} {port:<8} {st['count']:<10} "
            f"{st['bytes']//1024:<8} {st['photon_sig']:<8} "
            f"{st['photon_strings']:<8} {zones_str}"
        )

    # Ports avec signatures Photon
    print("\n--- Ports avec signature Photon (0xF3) ---\n")
    photon_ports = [(k, v) for k, v in sorted_ports if v["photon_sig"] > 0]
    if photon_ports:
        for (proto, port), st in photon_ports:
            print(f"  {proto} port {port}: {st['photon_sig']} signatures Photon, "
                  f"{st['photon_strings']} strings, "
                  f"hex={st['sample_hex']}")
    else:
        print("  AUCUN port avec signature Photon detecte !")

    # Ports avec noms de zone
    print("\n--- Ports contenant des noms de zone ---\n")
    zone_ports = [(k, v) for k, v in sorted_ports if v["zone_found"]]
    if zone_ports:
        for (proto, port), st in zone_ports:
            unique_zones = list(set(st["zone_found"]))[:5]
            print(f"  {proto} port {port}: zones = {unique_zones}")
    else:
        print("  AUCUN nom de zone detecte dans le trafic !")
        print("  Possibilites :")
        print("    - Tu n'as pas traverse de portail pendant la capture")
        print("    - Le jeu chiffre les noms de zone")
        print("    - Le jeu utilise un format different")

    # Resume
    print(f"\n{'='*70}")
    if zone_ports:
        best = zone_ports[0]
        print(f"  RESULTAT : Les zones passent par {best[0][0]} port {best[0][1]}")
        print(f"  Copie ce resultat et envoie-le dans le chat !")
    elif photon_ports:
        non_standard = [p for p in photon_ports if p[0][1] not in (5055, 5056)]
        if non_standard:
            print(f"  INDICE : Trafic Photon detecte sur des ports non-standard :")
            for (proto, port), st in non_standard:
                print(f"    {proto} port {port} ({st['photon_sig']} signatures)")
        else:
            print("  Trafic Photon uniquement sur ports 5055/5056 mais pas de zones.")
            print("  Le jeu semble ne pas envoyer les noms de zone en clair.")
    else:
        print("  AUCUN trafic Photon detecte du tout.")
        print("  Verifie que le jeu est lance et que Npcap est installe.")
    print(f"{'='*70}\n")

    input("Appuie sur Entree pour fermer...")


if __name__ == "__main__":
    main()
