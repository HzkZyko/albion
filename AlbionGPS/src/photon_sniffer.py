"""
Sniffer Photon pour detecter automatiquement la zone courante dans
Albion Online.

Fonctionnement v2 :
- AsyncSniffer scapy en parallele sur toutes les cartes reseau actives
  (WiFi, Ethernet, adaptateurs virtuels...).
- Chaque paquet UDP 5056 est decode via photon_proto.parse_photon_packet
  qui extrait les events, op requests et op responses avec leurs
  parametres typee.
- Pour chaque message decode, tous les parametres (strings et ints) sont
  testes contre un WorldIndex charge depuis data/world.xml. Quand un
  parametre matche un identifiant de cluster connu, on ajoute un vote
  pour la zone correspondante.
- Systeme de vote glissant (fenetre de WINDOW_SECONDS, MIN_VOTES minimum)
  pour absorber le bruit et ne basculer la zone courante que quand on est
  certain.
- Mode dump : les paquets sont aussi ecrits en brut dans
  data/photon_capture.log (buffering=0) pour pouvoir etre analyses hors
  ligne en cas de probleme. Le dump est plafonne a 10 MB pour ne pas
  exploser le disque.

Threading : tout ce qui est appele depuis le thread de capture (le
callback on_zone_change et on_error) est execute en dehors du thread UI
Qt, donc le code appelant doit utiliser QMetaObject.invokeMethod ou
signals Qt pour remonter dans le thread principal.

Pas de droits admin = aucun paquet capture. Le run.bat de ce projet
demande une elevation automatique.
"""

from __future__ import annotations

import struct
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Iterable, Optional

from .photon_proto import FragmentBuffer, PhotonMessage, parse_photon_packet
from .world_index import WorldIndex


# Albion Online utilise DEUX ports Photon : 5055 (historique) et 5056
# (ajoute plus recemment). Source : albiondata-client/client/listener.go
# qui filtre sur "tcp port %d || udp port %d" pour chacun des deux ports.
# On ecoute les deux en UDP ; le jeu n'utilise pas TCP en pratique pour
# la telemetrie gameplay mais on inclut les deux par securite.
PHOTON_PORTS = (5055, 5056)
PHOTON_PORT = 5056  # garde pour retro-compat (tests)

# Parametres du vote glissant. Regles de pouce :
# - WINDOW_SECONDS : duree sur laquelle on compte les apparitions d'un
#   nom de zone. 4s donne un bon compromis reactivite / stabilite.
# - MIN_VOTES : il faut au moins ce nombre d'apparitions sur la fenetre
#   pour que la zone devienne "courante". Evite de basculer a cause d'un
#   paquet isole qui mentionnerait une autre zone.
WINDOW_SECONDS = 4.0
MIN_VOTES = 3


@dataclass
class _Vote:
    zone: str
    ts: float
    tuple_key: tuple  # (kind, code, param_key) - d'ou vient ce vote


# Liste blanche de tuples (kind, code, param_key) connus pour contenir le
# cluster du joueur. En mode TRUSTED seulement, ces tuples votent
# immediatement. En mode DISCOVERY (TRUSTED_TUPLES vide), tous les matchs
# strings votent, ce qui est maintenant sur puisque le matching int a ete
# desactive (voir _match_value) : les strings Photon qui ressemblent a un
# nom de cluster Albion sont extremement rares en dehors des vrais
# contextes de location.
#
# Reference OpJoin : albiondata-client/client/operation_join.go
#   operationJoinResponse.Location = mapstructure:"8" sur opcode 2
# Cette reponse est fragmentee (cf FragmentBuffer dans photon_proto.py) et
# ne fire qu'a la connexion / traversee de portail.
#
# On laisse la whitelist VIDE par defaut : en pratique, n'importe quel
# string de cluster recu est un bon indicateur, et le systeme de vote
# (MIN_VOTES + fenetre glissante) filtre le bruit residuel. Si on veut
# durcir la detection, ajouter ("op_response", 2, 8) ici.
TRUSTED_TUPLES: set[tuple[str, int, int]] = set()


# Tuples (kind, code, param_key) dont la valeur string est DIRECTEMENT le
# cluster courant. Sert a detecter les hideouts, islands, expeditions et
# les vrais clusters open-world.
#
# IMPORTANT : l'opcode OpJoin chez Albion est **1**, pas 2. On l'a confirme
# en instrumentant l'echantillonneur d'op_responses : apres traversee de
# portail on voit "op#1 keys=[0,1,2,3,4,6,7,8] p8='3207'" ou "3207" est le
# short cluster ID de Blackthorn Quarry. La doc albiondata-client
# (operation_join.go) dit code 2 mais cette branche est soit obsolete soit
# differente du build actuel. On garde 2 en fallback par securite.
#
# Valeur attendue : string representant le cluster. Trois formats possibles :
#   - short ID numerique, ex. "3207"  (le plus frequent d'apres l'obs)
#   - display name, ex. "Blackthorn Quarry"
#   - filename stem, ex. "3207_WRL_HL_AUTO_T4_UND_ROY.cluster.xml"
#   - hideout/island : "@HIDEOUT@..." ou "@ISLAND@..."
# On tente toutes les resolutions successivement dans _match_message.
_GROUND_TRUTH_TUPLES: set[tuple[str, int, int]] = {
    ("op_response", 1, 8),
    ("op_response", 2, 8),
}


def _normalize_location_string(raw: str) -> Optional[str]:
    """Transforme une valeur Location brute en nom d'affichage lisible.

    Cas :
    - "Blackthorn Quarry"  -> "Blackthorn Quarry"
    - "3207_WRL_HL_..."    -> "3207_WRL_HL_..." (garde tel quel, WorldIndex
                              s'en chargerait normalement mais ici on prend
                              la verite serveur telle quelle)
    - "@HIDEOUT@TNL-361@uuid"           -> "Hideout TNL-361"
    - "@ISLAND@..." / "@EXPEDITION@..." -> "Island ..." / "Expedition ..."
    - ""                    -> None (ignore)
    """
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("@"):
        # Format "@TYPE@identifier@uuid" ou "@TYPE@identifier"
        parts = s.split("@")
        # parts[0] == "", parts[1] == type, parts[2] == identifier (peut-etre)
        if len(parts) >= 3 and parts[1]:
            kind = parts[1].capitalize()  # HIDEOUT -> Hideout
            ident = parts[2] if parts[2] else "?"
            return f"{kind} {ident}"
        return s
    return s


class PhotonSniffer:
    """Sniffeur Photon non-bloquant, thread-safe.

    Usage :
        sniffer = PhotonSniffer(known_zones, on_change=lambda name: ...)
        sniffer.start()
        ...
        sniffer.stop()
    """

    def __init__(
        self,
        world_index: WorldIndex,
        on_zone_change: Callable[[str], None],
        on_error: Optional[Callable[[str], None]] = None,
        on_market_event: Optional[Callable] = None,
        dump_path: Optional[Path] = None,
        dump_max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._index = world_index
        self._on_change = on_zone_change
        self._on_error = on_error or (lambda _msg: None)
        self._sniffers: list = []  # List[AsyncSniffer] - populated at start()
        self._stop_event = threading.Event()
        self._votes: Deque[_Vote] = deque()
        self._current: Optional[str] = None
        self._lock = threading.Lock()
        self._running = False
        # Stats pour diagnostic : combien de paquets Photon le sniffer a vu
        # passer, combien contenaient au moins un match de zone, combien
        # de messages Photon ont ete decodes.
        self._packets_seen = 0
        self._packets_with_zone = 0
        self._messages_decoded = 0
        # Liste des noms d'interfaces reseau sur lesquelles on ecoute.
        self._ifaces_listening: list[str] = []
        # Dump file : ecriture brute des paquets (buffering=0) pour analyse
        # hors ligne en cas de probleme de detection.
        self._dump_path = dump_path
        self._dump_max_bytes = dump_max_bytes
        self._dump_file = None  # file handle lazily opened in start()
        self._dump_written = 0
        # Candidats detectes mais non confirmes, pour remontee UI diagnostic.
        self._last_candidate: Optional[str] = None
        # Stats par tuple (kind, code, param_key) : Counter{zone: count}.
        # Sert a identifier, via l'UI et analyze_photon_log.py, quel
        # parametre contient vraiment le cluster du joueur.
        self._tuple_stats: dict[tuple, Counter] = defaultdict(Counter)
        # Dernier timestamp + derniere zone vus pour chaque tuple. Permet
        # de reperer en direct le tuple qui vient de changer quand le
        # joueur traverse un portail.
        self._tuple_last: dict[tuple, tuple[float, str]] = {}
        # Log circulaire des 20 derniers matchs pour diagnostic UI :
        # (timestamp, tuple_key, zone)
        self._recent_matches: Deque[tuple[float, tuple, str]] = deque(maxlen=20)
        # Buffer de reassemblage des fragments Photon (crucial pour OpJoin
        # responses qui sont systematiquement fragmentees).
        self._fragment_buffer = FragmentBuffer()
        # Log circulaire des strings trouvees en RAW scan (bypass parseur
        # Photon complet) : permet de savoir si le probleme vient du
        # decodeur ou du filtrage de capture.
        self._raw_strings_seen: Deque[str] = deque(maxlen=40)
        self._raw_zone_matches: Deque[tuple[float, str]] = deque(maxlen=20)
        self._raw_strings_total = 0
        self._raw_strings_zone = 0
        # Compteurs de diagnostic pipeline Photon. Nous permettent d'isoler
        # ou le pipeline se casse : capture (_packets_seen), decodage
        # (_messages_decoded), reassemblage fragments, types de messages.
        self._fragments_received = 0
        self._fragments_assembled = 0  # groupes reassembles complets
        self._msg_events = 0
        self._msg_op_requests = 0
        self._msg_op_responses = 0
        # Echantillon des dernieres op_responses decodees, pour comprendre
        # quels opcodes reellement passent. Chaque entree = (code, param_preview)
        # ou param_preview = "key1=type1 key2=type2 ..." limite a 6 params.
        self._op_response_samples: Deque[str] = deque(maxlen=8)
        self._event_samples: Deque[str] = deque(maxlen=8)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        # Ouverture du dump file avec buffering=0 pour que le fichier
        # contienne quelque chose meme en cas d'arret brutal.
        if self._dump_path is not None:
            try:
                self._dump_path.parent.mkdir(parents=True, exist_ok=True)
                self._dump_file = open(self._dump_path, "wb", buffering=0)
                self._dump_written = 0
            except OSError as e:
                self._on_error(f"Impossible d'ouvrir {self._dump_path} : {e}")
                self._dump_file = None
        try:
            self._spawn_sniffers()
        except Exception as e:  # pragma: no cover - safety net
            self._on_error(f"Impossible de demarrer le sniffer : {e}")
            self._running = False
            return
        self._running = True

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        for s in self._sniffers:
            try:
                s.stop(join=False)
            except Exception:
                pass
        self._sniffers.clear()
        self._ifaces_listening.clear()
        self._fragment_buffer.clear()
        if self._dump_file is not None:
            try:
                self._dump_file.close()
            except Exception:
                pass
            self._dump_file = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_zone(self) -> Optional[str]:
        with self._lock:
            return self._current

    @property
    def stats(self) -> tuple[int, int]:
        """(paquets Photon vus, paquets contenant un nom de zone)."""
        with self._lock:
            return (self._packets_seen, self._packets_with_zone)

    @property
    def extended_stats(self) -> dict[str, Any]:
        """Stats plus completes pour l'affichage diagnostic."""
        with self._lock:
            # On exclut les tuples qui voient plus de 3 zones distinctes
            # (par definition pas un indicateur de cluster courant).
            # On montre les candidats restants tries par recence du
            # dernier match -- c'est le signal qui va permettre de
            # distinguer les tuples statiques (home, spawn) des tuples
            # qui firent en live quand on bouge.
            now = time.time()
            candidates = []
            for tk, zones in self._tuple_stats.items():
                distinct = len(zones)
                if distinct > 3:
                    continue
                last_ts, last_zone = self._tuple_last.get(tk, (0.0, "?"))
                candidates.append((tk, zones, last_ts, last_zone))
            # Tri : d'abord par recence (plus recent en haut), puis par volume.
            candidates.sort(key=lambda x: (-x[2], -sum(x[1].values())))
            top_summary = []
            for (kind, code, pkey), zones, last_ts, last_zone in candidates[:8]:
                total = sum(zones.values())
                top_zone, top_count = zones.most_common(1)[0]
                distinct = len(zones)
                age = now - last_ts if last_ts else 999
                age_str = f"{int(age)}s" if age < 999 else "never"
                top_summary.append(
                    f"{kind}#{code}[{pkey}] last={last_zone} -{age_str} "
                    f"top={top_zone}({top_count}/{total},{distinct}z)"
                )
            # Derniers matchs bruts (timestamp, tuple, zone) - pour debug
            recent = []
            for ts, tk, z in list(self._recent_matches)[-10:]:
                age = int(now - ts)
                kind, code, pkey = tk
                recent.append(f"-{age:3d}s {kind}#{code}[{pkey}]->{z}")
            # Echantillon des dernieres strings brutes (non-matchees) pour
            # diagnostic : permet de voir a l'oeil si Albion envoie
            # des noms de cluster connus ou un autre format (hash, id, etc.)
            raw_sample = list(self._raw_strings_seen)[-8:]
            raw_zone = [
                f"-{int(now - ts)}s {s}"
                for ts, s in list(self._raw_zone_matches)[-5:]
            ]
            return {
                "packets_seen": self._packets_seen,
                "packets_with_zone": self._packets_with_zone,
                "messages_decoded": self._messages_decoded,
                "last_candidate": self._last_candidate,
                "ifaces": list(self._ifaces_listening),
                "current_zone": self._current or "(aucune)",
                "votes_total": len(self._votes),
                "dump_bytes": self._dump_written,
                "top_tuples": top_summary,
                "recent_matches": recent,
                "raw_strings_total": self._raw_strings_total,
                "raw_strings_zone": self._raw_strings_zone,
                "raw_sample": raw_sample,
                "raw_zone_matches": raw_zone,
                "msg_events": self._msg_events,
                "msg_op_requests": self._msg_op_requests,
                "msg_op_responses": self._msg_op_responses,
                "fragments_received": self._fragment_buffer.fragments_received,
                "groups_assembled": self._fragment_buffer.groups_assembled,
                "op_response_samples": list(self._op_response_samples),
                "event_samples": list(self._event_samples),
            }

    @property
    def interfaces(self) -> list[str]:
        """Liste des cartes reseau sur lesquelles on ecoute."""
        with self._lock:
            return list(self._ifaces_listening)

    # -------------------------------------------------------- interface scan

    def _list_interfaces(self) -> list[str]:
        """Enumere les cartes reseau Windows actives (avec au moins une IPv4
        assignee, hors loopback). C'est la meme strategie que
        albiondata-client : on sniffe toutes les interfaces en parallele
        parce qu'on ne peut pas deviner a l'avance laquelle porte le
        trafic du jeu (WiFi vs Ethernet vs adaptateurs virtuels)."""
        try:
            from scapy.arch.windows import get_windows_if_list  # type: ignore
        except ImportError:
            # Pas sur Windows ou scapy trop vieux : fallback sur get_if_list
            from scapy.all import get_if_list  # type: ignore
            return [i for i in get_if_list() if "loopback" not in i.lower()]

        ifaces: list[str] = []
        for info in get_windows_if_list():
            name = info.get("name") or info.get("description")
            if not name:
                continue
            lower = name.lower()
            # On exclut les interfaces clairement inutiles.
            if "loopback" in lower or "pseudo" in lower:
                continue
            ips = info.get("ips") or []
            # On garde si au moins une IPv4 non-link-local est assignee.
            has_ipv4 = any(
                ":" not in ip and not ip.startswith("169.254") and ip != "0.0.0.0"
                for ip in ips
            )
            if has_ipv4:
                ifaces.append(name)
        return ifaces

    # -------------------------------------------------------- sniff spawn

    def _spawn_sniffers(self) -> None:
        try:
            from scapy.all import AsyncSniffer, UDP, IP, conf  # type: ignore
        except ImportError:
            self._on_error(
                "scapy n'est pas installe. La detection auto de zone est desactivee."
            )
            return

        # On force l'utilisation du backend pcap (Npcap sur Windows) :
        # c'est le seul qui donne acces a toutes les interfaces et au
        # filtre BPF natif. Sans ca, scapy tombe parfois sur un backend
        # raw-socket qui ne voit que le trafic de son interface par defaut.
        try:
            conf.use_pcap = True
        except Exception:
            pass

        ifaces = self._list_interfaces()
        if not ifaces:
            # En dernier recours, on laisse scapy choisir.
            ifaces = [None]  # type: ignore[list-item]

        with self._lock:
            self._ifaces_listening = [i for i in ifaces if i is not None]

        def make_handler():
            def handle(pkt) -> None:
                if self._stop_event.is_set():
                    return
                if not pkt.haslayer(UDP) or not pkt.haslayer(IP):
                    return
                udp = pkt[UDP]
                if udp.sport not in PHOTON_PORTS and udp.dport not in PHOTON_PORTS:
                    return
                payload = bytes(udp.payload)
                if not payload:
                    return
                self._process_packet(payload)
            return handle

        handler = make_handler()
        # BPF filter couvrant les deux ports Photon simultanement. Meme
        # strategie que albiondata-client/client/listener.go.
        bpf_filter = " or ".join(f"udp port {p}" for p in PHOTON_PORTS)
        started = 0
        errors: list[str] = []
        for iface in ifaces:
            try:
                sniffer = AsyncSniffer(
                    iface=iface,
                    filter=bpf_filter,
                    prn=handler,
                    store=False,
                )
                sniffer.start()
                self._sniffers.append(sniffer)
                started += 1
            except Exception as e:
                errors.append(f"{iface}: {e}")

        if started == 0:
            msg = "Aucune interface reseau capturable."
            if errors:
                msg += " Details : " + " | ".join(errors[:3])
            self._on_error(msg)
        elif errors:
            # Info : on a demarre partiellement. On remonte quand meme
            # pour que l'utilisateur sache que certaines ifaces ont rate.
            self._on_error(
                f"Sniffer actif sur {started} interface(s). "
                f"Echecs : {len(errors)}"
            )

    # --------------------------------------------------------- packet processing

    def _process_packet(self, payload: bytes) -> None:
        """Point d'entree d'un paquet UDP 5056 recu. Incrementes les stats,
        dump en brut si configure, decode Photon, scanne chaque parametre
        contre le WorldIndex, et vote."""
        with self._lock:
            self._packets_seen += 1

        # Dump brut : plafonne a dump_max_bytes pour ne pas saturer le disque
        if self._dump_file is not None and self._dump_written < self._dump_max_bytes:
            try:
                # Format : timestamp (double BE), length (uint32 BE), payload
                header = struct.pack(">dI", time.time(), len(payload))
                self._dump_file.write(header)
                self._dump_file.write(payload)
                self._dump_written += len(header) + len(payload)
                # Fsync a chaque ecriture : on privilegie la durabilite (on
                # veut pouvoir lire le dump meme si l'app n'a pas encore
                # ete fermee proprement) sur la perf disque. Les volumes
                # restent petits (quelques dizaines de Ko/s max).
                try:
                    self._dump_file.flush()
                    import os
                    os.fsync(self._dump_file.fileno())
                except Exception:
                    pass
            except OSError:
                # disque plein ou fichier ferme : on continue sans dump
                pass

        # --- RAW SCAN : bypass complet du parseur Photon ---
        # On cherche le pattern Photon "string" brut dans le payload :
        # type byte 0x73, puis u16 BE longueur, puis N bytes UTF-8. Ca
        # marche meme si le reassemblage des fragments echoue, meme si le
        # parseur decroche sur un type exotique, meme si le message n'a
        # pas la signature 0xF3 attendue. C'est notre filet de securite.
        self._raw_scan(payload)

        try:
            messages = parse_photon_packet(payload, self._fragment_buffer)
        except Exception:
            return

        if not messages:
            return

        with self._lock:
            self._messages_decoded += len(messages)
            for m in messages:
                if m.kind == "event":
                    self._msg_events += 1
                    if len(self._event_samples) < 8 or m.code not in (1,):
                        # On preserve quelques echantillons d'events pour
                        # voir quels codes passent. On saute event#1 (trop
                        # frequent) apres 8 echantillons.
                        if m.code != 1:
                            self._event_samples.append(
                                f"ev#{m.code} keys={sorted(m.params.keys())[:6]}"
                            )
                elif m.kind == "op_request":
                    self._msg_op_requests += 1
                elif m.kind == "op_response":
                    self._msg_op_responses += 1
                    # Toujours echantillonner les op_responses (rares et
                    # cruciales). Format : "op#<code> keys=[1,2,8,...]
                    # val8=<preview ou ???>".
                    keys = sorted(m.params.keys())
                    val8 = m.params.get(8)
                    if val8 is None:
                        val8_preview = "no-p8"
                    elif isinstance(val8, str):
                        val8_preview = f'p8="{val8[:30]}"'
                    else:
                        val8_preview = f"p8={type(val8).__name__}"
                    self._op_response_samples.append(
                        f"op#{m.code} keys={keys[:8]} {val8_preview}"
                    )

        matched_this_packet = False
        for msg in messages:
            for tuple_key, zone in self._match_message(msg):
                matched_this_packet = True
                # Track per-tuple stats even for untrusted tuples (for
                # diagnostic / analyze_photon_log.py).
                with self._lock:
                    now_ts = time.time()
                    self._tuple_stats[tuple_key][zone] += 1
                    self._tuple_last[tuple_key] = (now_ts, zone)
                    self._recent_matches.append((now_ts, tuple_key, zone))
                    self._last_candidate = (
                        f"{tuple_key[0]}#{tuple_key[1]}[{tuple_key[2]}] -> {zone}"
                    )
                # Si une whitelist est active, on ne vote que pour les tuples
                # connus. Sinon on vote pour tout (mode decouverte).
                if TRUSTED_TUPLES and tuple_key not in TRUSTED_TUPLES:
                    continue
                self._add_vote(zone, tuple_key)

        if matched_this_packet:
            with self._lock:
                self._packets_with_zone += 1

    # ------------------------------------------------------------------
    # Helpers pour _raw_scan : decodage varint LEB128 (Protocol18)
    # ------------------------------------------------------------------
    @staticmethod
    def _read_varint(data: bytes, offset: int) -> tuple[int, int] | None:
        """Decode un varint LEB128 a partir de *offset*.

        Retourne (value, new_offset) ou None si on depasse la fin de *data*
        ou si le varint fait plus de 5 bytes (protection anti-boucle).
        """
        result = 0
        shift = 0
        pos = offset
        n = len(data)
        for _ in range(5):           # max 5 bytes pour un int32
            if pos >= n:
                return None
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                return result, pos
            shift += 7
        return None                  # varint trop long, probablement faux

    def _raw_scan(self, payload: bytes) -> None:
        """Scan brut du payload UDP a la recherche de strings Photon.

        On scanne deux formats en parallele :

        * **Protocol18** (post Radiant Wilds) :
          type byte ``7``, longueur en varint LEB128, puis N bytes UTF-8.

        * **Legacy** (ancien format) :
          type byte ``0x73``, longueur u16 big-endian, puis N bytes UTF-8.

        Chaque string trouvee est testee contre le WorldIndex. Si ca matche
        un cluster, on ajoute un vote sous le tuple synthetique ('raw',0,0).
        Ca permet de detecter la zone meme si le parseur Photon complet
        rate le message (fragments non reassembles, type exotique, etc.)."""
        n = len(payload)
        i = 0
        tk = ("raw", 0, 0)
        now_ts = time.time()
        while i < n - 2:
            b = payload[i]

            # --- Protocol18 string : type=7 + varint length + UTF-8 ---
            if b == 0x07:
                vi = self._read_varint(payload, i + 1)
                if vi is not None:
                    length, data_start = vi
                    if 3 <= length <= 80 and data_start + length <= n:
                        raw = payload[data_start : data_start + length]
                        if all(32 <= c < 127 for c in raw):
                            try:
                                s = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                i += 1
                                continue
                            with self._lock:
                                self._raw_strings_total += 1
                                self._raw_strings_seen.append(s)
                            m = self._index.lookup_string(s)
                            if m:
                                with self._lock:
                                    self._raw_strings_zone += 1
                                    self._raw_zone_matches.append(
                                        (now_ts, f"{s}->{m}"))
                                    self._tuple_stats[tk][m] += 1
                                    self._tuple_last[tk] = (now_ts, m)
                                    self._recent_matches.append(
                                        (now_ts, tk, m))
                                    self._last_candidate = f"raw -> {m}"
                                self._add_vote(m, tk)
                            i = data_start + length
                            continue
                # Pas un varint valide ou longueur hors bornes -> avancer
                i += 1
                continue

            # --- Legacy string : type=0x73 + u16 BE length + UTF-8 ---
            if b == 0x73 and i + 2 < n:
                length = (payload[i + 1] << 8) | payload[i + 2]
                if 3 <= length <= 80 and i + 3 + length <= n:
                    raw = payload[i + 3 : i + 3 + length]
                    if all(32 <= c < 127 for c in raw):
                        try:
                            s = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            i += 1
                            continue
                        with self._lock:
                            self._raw_strings_total += 1
                            self._raw_strings_seen.append(s)
                        m = self._index.lookup_string(s)
                        if m:
                            with self._lock:
                                self._raw_strings_zone += 1
                                self._raw_zone_matches.append(
                                    (now_ts, f"{s}->{m}"))
                                self._tuple_stats[tk][m] += 1
                                self._tuple_last[tk] = (now_ts, m)
                                self._recent_matches.append(
                                    (now_ts, tk, m))
                                self._last_candidate = f"raw -> {m}"
                            self._add_vote(m, tk)
                        i += 3 + length
                        continue

            i += 1

    def _match_message(self, msg: PhotonMessage):
        """Yield (tuple_key, zone) pour chaque match trouve dans un message.

        tuple_key = (kind, code, param_key). Recurse dans les listes/dicts
        mais rapporte la param_key racine (pas le chemin complet) - c'est
        suffisant pour discriminer les sources.

        CAS SPECIAL : pour les "tuples de verite" (OpJoin response param 8
        = Location), on prend la valeur brute comme zone meme si elle n'est
        pas dans le WorldIndex. Ca couvre les hideouts, islands, expeditions,
        arenas, et autres contextes non-open-world pour lesquels world.xml
        n'a pas d'entree."""
        for key, value in msg.params.items():
            tkey = (msg.kind, msg.code, key)
            if tkey in _GROUND_TRUTH_TUPLES:
                resolved = self._resolve_ground_truth_value(value)
                if resolved:
                    yield tkey, resolved
                    continue  # priorite : on ne matche pas aussi via WorldIndex
            for zone in self._match_value(value):
                yield tkey, zone

    def _resolve_ground_truth_value(self, value: Any) -> Optional[str]:
        """Resout une valeur provenant d'un tuple ground-truth en nom de
        zone. Tente successivement : lookup_string, lookup_int (si la
        valeur est un short ID numerique comme "3207"), normalisation
        (@HIDEOUT@...), et en dernier recours la valeur brute.

        Retourne None si la valeur est vide/inutile."""
        if value is None:
            return None
        # Cas string : tentatives successives
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            # 1) Nom connu dans WorldIndex (display name ou filename)
            m = self._index.lookup_string(s)
            if m:
                return m
            # 2) Short numeric ID comme "3207" -> int -> lookup_int
            # On autorise ce fallback uniquement dans le contexte ground
            # truth : hors contexte ce serait la meme collision desastreuse
            # qu'avant (int 1000 omnipresent = Lymhurst partout).
            if s.isdigit() and len(s) <= 6:
                try:
                    as_int = int(s)
                except ValueError:
                    as_int = -1
                if as_int >= 0:
                    m = self._index.lookup_int(as_int)
                    if m:
                        return m
            # 3) Format @HIDEOUT@.../@ISLAND@... -> normalisation lisible
            display = _normalize_location_string(s)
            if display:
                return display
            return None
        # Cas int direct (rare mais possible)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            m = self._index.lookup_int(value)
            return m
        return None

    def _match_value(self, value: Any):
        """Yield tous les display_names de zones trouves dans une valeur
        (recursif pour listes / dicts / bytes).

        IMPORTANT : on ne matche QUE les strings. Matcher les int contre le
        WorldIndex etait une fausse bonne idee : les clusters ont des IDs
        comme 1000 (Lymhurst), 3004 (Martlock), 3207 (Blackthorn Quarry)
        qui entrent en collision avec n'importe quel int omnipresent dans
        le trafic Photon (object IDs, item IDs, HP, skill cooldowns, etc.),
        ce qui provoque des dizaines de faux positifs par paquet. Albion
        envoie systematiquement l'identifiant de cluster sous forme de
        string : soit le displayname ("Blackthorn Quarry"), soit le nom de
        fichier ("3207_WRL_HL_AUTO_T4_UND_ROY.cluster.xml"). Les deux sont
        indexes dans WorldIndex via lookup_string."""
        if value is None:
            return
        if isinstance(value, str):
            m = self._index.lookup_string(value)
            if m:
                yield m
            return
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            # volontairement ignore - cf docstring
            return
        if isinstance(value, (list, tuple)):
            for v in value:
                yield from self._match_value(v)
            return
        if isinstance(value, dict):
            for v in value.values():
                yield from self._match_value(v)
            return
        if isinstance(value, (bytes, bytearray)):
            try:
                s = bytes(value).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return
            if all(32 <= ord(c) < 127 for c in s):
                m = self._index.lookup_string(s)
                if m:
                    yield m
            return

    # --------------------------------------------------------------- voting

    def _add_vote(self, zone: str, tuple_key: tuple) -> None:
        now = time.time()
        fire: Optional[str] = None
        with self._lock:
            self._votes.append(_Vote(zone=zone, ts=now, tuple_key=tuple_key))
            # Purge des votes trop vieux
            cutoff = now - WINDOW_SECONDS
            while self._votes and self._votes[0].ts < cutoff:
                self._votes.popleft()

            # Comptage sur la fenetre
            counts: dict[str, int] = {}
            for v in self._votes:
                counts[v.zone] = counts.get(v.zone, 0) + 1

            # Strategie 1 : zone majoritaire sur la fenetre avec >=MIN_VOTES
            # (pour les cas ou l'evenement fire plusieurs fois rapidement)
            winner, votes = max(counts.items(), key=lambda kv: kv[1])
            if votes >= MIN_VOTES and winner != self._current:
                self._current = winner
                fire = winner

            # Strategie 2 : si aucun candidat n'atteint MIN_VOTES mais qu'on
            # voit un burst frais dans un SEUL tuple (>=3 matchs recents
            # dans ce tuple, tous pour la meme zone), on accepte. Ca
            # couvre les "Join events" qui firent en rafale courte quand
            # le joueur traverse un portail.
            if fire is None and zone != self._current:
                tuple_recent_count = sum(
                    1 for v in self._votes
                    if v.tuple_key == tuple_key and v.zone == zone
                )
                # Verifie aussi que ce tuple n'a jamais vu d'autre zone
                # sur la fenetre (pas de bruit cross-zone)
                tuple_zones_in_window = {
                    v.zone for v in self._votes if v.tuple_key == tuple_key
                }
                if tuple_recent_count >= 3 and len(tuple_zones_in_window) == 1:
                    self._current = zone
                    fire = zone

            # Strategie 3 : tuple ground-truth = source de verite serveur
            # authoritative (OpJoin response.Location). 1 match suffit a
            # basculer, car OpJoin ne fire qu'une seule fois par traversee
            # de portail et attendre MIN_VOTES serait contre-productif.
            if fire is None and tuple_key in _GROUND_TRUTH_TUPLES and zone != self._current:
                self._current = zone
                fire = zone

        if fire is not None:
            try:
                self._on_change(fire)
            except Exception as e:  # pragma: no cover
                self._on_error(f"Callback on_zone_change a leve : {e}")
