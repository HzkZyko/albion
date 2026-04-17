"""
Outil de calibration : capture brute des paquets Photon d'Albion Online.

Objectif : identifier, sur la version actuelle du client, quel event / quel
parametre contient le nom de la zone courante quand le joueur change de map.
On tourne ce script pendant quelques minutes en changeant volontairement de
zone a intervalles reguliers, puis on analyse le log genere pour ecrire la
vraie detection dans src/photon_sniffer.py.

Usage (depuis AlbionGPS/, en admin) :
    .venv\\Scripts\\python.exe tools\\capture_photon.py

Ce que ca fait en direct :
- Sniffe UDP 5056 (port Photon d'Albion) avec scapy.
- Ecrit chaque paquet brut dans data/photon_capture.log (binaire).
- Scanne le payload a la recherche de strings Photon (marqueur 0x73 +
  int16 BE longueur). Si une string matche un nom de zone connu de
  data/zones.json, l'imprime avec un timestamp.
- S'arrete proprement avec Ctrl+C.

Ca ne decodage PAS le protocole Photon en entier : le but est juste
d'avoir un log exploitable pour la phase suivante.
"""

from __future__ import annotations

import json
import signal
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from scapy.all import AsyncSniffer, UDP, IP, conf  # type: ignore
except ImportError:
    print("[ERREUR] scapy n'est pas installe. Lance run.bat une fois pour")
    print("        creer le venv et installer les dependances, puis relance")
    print("        ce script via .venv\\Scripts\\python.exe tools\\capture_photon.py")
    sys.exit(1)

# Force Npcap backend (cf. src/photon_sniffer.py pour le rationnel)
try:
    conf.use_pcap = True
except Exception:
    pass


def list_active_ifaces() -> list[str]:
    try:
        from scapy.arch.windows import get_windows_if_list  # type: ignore
    except ImportError:
        from scapy.all import get_if_list  # type: ignore
        return [i for i in get_if_list() if "loopback" not in i.lower()]
    ifaces = []
    for info in get_windows_if_list():
        name = info.get("name") or info.get("description")
        if not name:
            continue
        if "loopback" in name.lower() or "pseudo" in name.lower():
            continue
        ips = info.get("ips") or []
        if any(":" not in ip and not ip.startswith("169.254") and ip != "0.0.0.0" for ip in ips):
            ifaces.append(name)
    return ifaces


PHOTON_PORT = 5056
DATA_DIR = ROOT / "data"
LOG_PATH = DATA_DIR / "photon_capture.log"


def load_zone_names() -> set[str]:
    """Charge tous les noms de zones connus depuis zones.json."""
    path = DATA_DIR / "zones.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data["zones"].keys())


def scan_photon_strings(payload: bytes) -> list[str]:
    """Cherche les strings serialisees Photon dans un payload brut.

    Format Photon pour type 's' (string) : 1 byte 0x73, 2 bytes BE longueur,
    puis N bytes UTF-8. On accepte tout pattern plausible puis on filtrera
    en comparant a la liste des zones connues.
    """
    results: list[str] = []
    n = len(payload)
    i = 0
    while i < n - 3:
        if payload[i] == 0x73:  # 's' = string
            (length,) = struct.unpack_from(">H", payload, i + 1)
            if 1 <= length <= 64 and i + 3 + length <= n:
                raw = payload[i + 3 : i + 3 + length]
                try:
                    s = raw.decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                # On garde uniquement les strings imprimables
                if all(32 <= ord(c) < 127 for c in s):
                    results.append(s)
                i += 3 + length
                continue
        i += 1
    return results


class Capture:
    def __init__(self) -> None:
        self.zone_names = load_zone_names()
        # On rend les noms insensibles a la casse et aux espaces pour
        # maximiser les matchs (le client peut utiliser des variantes).
        self.zone_names_lower = {z.lower(): z for z in self.zone_names}
        self.packet_count = 0
        self.matched_count = 0
        self.last_match: str | None = None
        self.running = True
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # buffering=0 : ecriture directe sur le disque, pas de buffer
        # Python. Comme ca meme si tu fermes la console au lieu de Ctrl+C,
        # le fichier contient tout ce qui a ete capture.
        self.log_file = open(LOG_PATH, "wb", buffering=0)
        print(f"[OK] Log binaire : {LOG_PATH}")
        print(f"[OK] {len(self.zone_names)} noms de zones charges pour le matching.")
        print("[INFO] Change de zone dans Albion. Ctrl+C pour arreter.\n")

    def handle(self, pkt) -> None:
        if not pkt.haslayer(UDP) or not pkt.haslayer(IP):
            return
        udp = pkt[UDP]
        if udp.sport != PHOTON_PORT and udp.dport != PHOTON_PORT:
            return
        payload = bytes(udp.payload)
        if not payload:
            return
        self.packet_count += 1

        ts = time.time()
        direction = "IN " if udp.sport == PHOTON_PORT else "OUT"
        ip = pkt[IP]

        # --- ecriture binaire (timestamp, dir, src, dst, len, payload) ---
        header = struct.pack(
            ">dB15s15sI",
            ts,
            1 if direction == "IN " else 0,
            ip.src.encode("ascii")[:15].ljust(15, b"\x00"),
            ip.dst.encode("ascii")[:15].ljust(15, b"\x00"),
            len(payload),
        )
        self.log_file.write(header)
        self.log_file.write(payload)

        # --- scan live pour matcher un nom de zone connu ---
        strings = scan_photon_strings(payload)
        for s in strings:
            key = s.lower()
            if key in self.zone_names_lower:
                name = self.zone_names_lower[key]
                if name != self.last_match:
                    self.matched_count += 1
                    self.last_match = name
                    stamp = time.strftime("%H:%M:%S", time.localtime(ts))
                    print(
                        f"[{stamp}] {direction} {ip.src}->{ip.dst}  "
                        f"ZONE CANDIDATE: {name}"
                    )

        if self.packet_count % 200 == 0:
            print(
                f"  ... {self.packet_count} paquets Photon captures, "
                f"{self.matched_count} matchs de zone"
            )

    def stop(self, *_: object) -> None:
        self.running = False

    def close(self) -> None:
        self.log_file.close()
        print()
        print(f"[FIN] {self.packet_count} paquets Photon ecrits dans {LOG_PATH}")
        print(f"[FIN] {self.matched_count} matchs de nom de zone detectes.")
        if self.last_match is None:
            print(
                "[WARN] Aucun nom de zone detecte. Verifie que tu joues bien "
                "et que Npcap est bien installe avec le mode WinPcap."
            )


def main() -> None:
    cap = Capture()
    signal.signal(signal.SIGINT, cap.stop)

    ifaces = list_active_ifaces()
    if not ifaces:
        print("[WARN] Aucune interface active detectee, scapy choisira lui-meme.")
        ifaces = [None]  # type: ignore[list-item]
    else:
        print(f"[OK] Sniffing sur {len(ifaces)} interface(s) :")
        for i in ifaces:
            print(f"       - {i}")
    print()

    sniffers = []
    for iface in ifaces:
        try:
            s = AsyncSniffer(
                iface=iface,
                filter=f"udp port {PHOTON_PORT}",
                prn=cap.handle,
                store=False,
            )
            s.start()
            sniffers.append(s)
        except Exception as e:
            print(f"[WARN] iface {iface} : {e}")

    if not sniffers:
        print("[ERREUR] Aucune interface n'a pu etre ouverte.")
        print("         Verifie que Npcap est installe (npcap.com) avec")
        print("         l'option 'WinPcap API-compatible Mode' cochee, et")
        print("         que tu lances ce script en administrateur.")
        cap.close()
        return

    try:
        while cap.running:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        for s in sniffers:
            try:
                s.stop(join=False)
            except Exception:
                pass
        cap.close()


if __name__ == "__main__":
    main()
