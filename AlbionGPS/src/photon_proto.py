"""
Decodeur minimal du protocole Photon (ExitGames) utilise par Albion Online.

On ne cherche pas a etre exhaustif : on decode juste assez pour extraire les
events, op requests et op responses, avec leurs parametres typee. Ca nous
permet ensuite d'identifier dans quel parametre de quel message Albion
encode la zone courante.

Reference : https://doc.photonengine.com/content/common/binary-protocol.html
Et les implementations en Go / Rust / C# du projet Albion Data.

Limites volontaires :
- Pas de CRC check.
- Pas de decompression de types exotiques (custom / dictionary complexes).
  Si un type inconnu est rencontre, on arrete de parser ce message-la et
  on passe au suivant, sans crasher.

Le reassemblage des fragments (CMD_SEND_FRAGMENT) est supporte via la
classe FragmentBuffer : passe une instance a parse_photon_packet() et
elle bufferise les fragments partiels jusqu'a avoir toutes les parties
d'un groupe, puis parse le message reconstitue. C'est crucial pour
OperationJoin response qui est systematiquement fragmentee.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Optional


# ---- Photon protocol constants ---------------------------------------------

# Command types
CMD_ACK = 1
CMD_CONNECT = 2
CMD_VERIFY_CONNECT = 3
CMD_DISCONNECT = 4
CMD_PING = 5
CMD_SEND_RELIABLE = 6
CMD_SEND_UNRELIABLE = 7
CMD_SEND_FRAGMENT = 8

# Message types (inside a reliable/unreliable command payload)
MSG_OP_REQUEST = 2
MSG_RESULT = 3
MSG_EVENT = 4
MSG_OP_RESPONSE = 7  # alternative response format


# ---- Decoded message dataclasses -------------------------------------------


@dataclass
class PhotonMessage:
    kind: str  # 'event' | 'op_request' | 'op_response'
    code: int  # event code OR operation code
    params: dict[int, Any] = field(default_factory=dict)
    return_code: Optional[int] = None  # for op_response
    debug_message: Optional[str] = None  # for op_response


class FragmentBuffer:
    """Stocke les fragments partiels d'un groupe de fragments Photon.

    Un groupe de fragments est identifie par son StartSequenceNumber. Quand
    tous les fragments d'un groupe sont recus, on concatene les payloads et
    on retourne le message reconstitue pour parsing.

    Thread-unsafe : utilise une instance par sniffer. Le sniffer possede
    deja son propre lock.
    """

    # Nombre max de groupes en cours de reception avant eviction FIFO.
    MAX_PENDING_GROUPS = 128

    def __init__(self) -> None:
        # start_seq -> (total_count, {frag_num: bytes})
        self._groups: dict[int, tuple[int, dict[int, bytes]]] = {}
        # ordre d'insertion pour eviction FIFO
        self._order: list[int] = []
        # Compteurs diagnostic accessibles de l'exterieur.
        self.fragments_received = 0
        self.groups_assembled = 0

    def add(
        self,
        start_seq: int,
        frag_count: int,
        frag_num: int,
        payload: bytes,
    ) -> Optional[bytes]:
        """Ajoute un fragment. Retourne les bytes reassembles si tous les
        fragments du groupe sont presents, sinon None."""
        self.fragments_received += 1
        if frag_count <= 0 or frag_count > 1000:
            return None  # valeurs aberrantes
        if frag_num < 0 or frag_num >= frag_count:
            return None

        if start_seq not in self._groups:
            # Nouveau groupe
            if len(self._order) >= self.MAX_PENDING_GROUPS:
                # Eviction FIFO : drop le plus vieux groupe incomplet
                evict = self._order.pop(0)
                self._groups.pop(evict, None)
            self._groups[start_seq] = (frag_count, {})
            self._order.append(start_seq)

        total_count, frags = self._groups[start_seq]
        # Coherence : si un fragment avec frag_count different arrive, on
        # fait confiance au dernier (cas rare de reuse de seq)
        if total_count != frag_count:
            self._groups[start_seq] = (frag_count, {frag_num: payload})
            return None

        frags[frag_num] = payload
        if len(frags) >= total_count:
            # Tous les fragments sont arrives : on concatene dans l'ordre
            try:
                assembled = b"".join(frags[i] for i in range(total_count))
            except KeyError:
                return None
            # Nettoyage
            self._groups.pop(start_seq, None)
            try:
                self._order.remove(start_seq)
            except ValueError:
                pass
            self.groups_assembled += 1
            return assembled
        return None

    def clear(self) -> None:
        self._groups.clear()
        self._order.clear()


# ---- Helpers ----------------------------------------------------------------


class _Reader:
    """Cursor binaire big-endian, qui leve IndexError en cas de lecture hors
    limites. Le code appelant attrape et skippe le paquet pourri."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise IndexError("photon reader underrun")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.read(1)[0]

    def i8(self) -> int:
        return struct.unpack(">b", self.read(1))[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.read(8))[0]

    def f32(self) -> float:
        return struct.unpack(">f", self.read(4))[0]

    def f64(self) -> float:
        return struct.unpack(">d", self.read(8))[0]

    def string(self) -> str:
        length = self.u16()
        data = self.read(length)
        return data.decode("utf-8", errors="replace")

    def bool(self) -> bool:
        return self.u8() != 0


# ---- Typed value reader -----------------------------------------------------


def _read_value(r: _Reader, type_code: int) -> Any:
    """Lit une valeur Photon d'un type donne. Leve ValueError si type inconnu."""
    # GpType codes, cf. Photon SDK
    if type_code == 0x00:  # Null
        return None
    if type_code == 0x62:  # byte
        return r.u8()
    if type_code == 0x6D:  # bool
        return r.bool()
    if type_code == 0x6B:  # short (int16)
        return r.i16()
    if type_code == 0x69:  # int (int32)
        return r.i32()
    if type_code == 0x6C:  # long (int64)
        return r.i64()
    if type_code == 0x66:  # float
        return r.f32()
    if type_code == 0x64:  # double
        return r.f64()
    if type_code == 0x73:  # string
        return r.string()
    if type_code == 0x78:  # byte[]
        n = r.u32()
        return r.read(n)
    if type_code == 0x6E:  # int32[]
        n = r.u32()
        return [r.i32() for _ in range(n)]
    if type_code == 0x7A:  # string[]
        n = r.u16()
        return [r.string() for _ in range(n)]
    if type_code == 0x79:  # array (typed)
        n = r.u16()
        inner = r.u8()
        return [_read_value(r, inner) for _ in range(n)]
    if type_code == 0x61:  # array of string-arrays? (rare, traite comme 'y')
        n = r.u16()
        inner = r.u8()
        return [_read_value(r, inner) for _ in range(n)]
    if type_code == 0x68:  # Hashtable
        n = r.u16()
        out: dict[Any, Any] = {}
        for _ in range(n):
            kt = r.u8()
            k = _read_value(r, kt)
            vt = r.u8()
            v = _read_value(r, vt)
            out[k] = v
        return out
    if type_code == 0x44:  # Dictionary (typed)
        kt = r.u8()
        vt = r.u8()
        n = r.u16()
        out2: dict[Any, Any] = {}
        for _ in range(n):
            k = _read_value(r, kt)
            v = _read_value(r, vt)
            out2[k] = v
        return out2
    if type_code == 0x65:  # EventData (imbriquee)
        ev_code = r.u8()
        params = _read_param_table(r)
        return {"event_code": ev_code, "params": params}
    if type_code == 0x63:  # Custom
        custom_type = r.u8()
        n = r.u16()
        return {"custom": custom_type, "data": r.read(n)}
    raise ValueError(f"unknown photon type 0x{type_code:02x}")


def _read_param_table(r: _Reader) -> dict[int, Any]:
    count = r.u16()
    out: dict[int, Any] = {}
    for _ in range(count):
        key = r.u8()
        t = r.u8()
        try:
            out[key] = _read_value(r, t)
        except (ValueError, IndexError):
            # Type inconnu ou buffer corrompu : on s'arrete la mais on garde
            # les params deja decodes. Fiable meme sur du trafic exotique.
            break
    return out


# ---- Top-level packet parser ------------------------------------------------


def parse_photon_packet(
    payload: bytes,
    fragment_buffer: Optional[FragmentBuffer] = None,
) -> list[PhotonMessage]:
    """Decode un paquet Photon et retourne la liste des messages applicatifs
    trouves. Silencieusement tolerant aux paquets pourris.

    Si `fragment_buffer` est fourni, les commandes CMD_SEND_FRAGMENT sont
    bufferisees et les messages sont parses des que le groupe est complet.
    Sans buffer, les fragments sont ignores (perte des OpJoin responses).
    """

    messages: list[PhotonMessage] = []
    if len(payload) < 12:
        return messages

    try:
        r = _Reader(payload)
        _peer_id = r.u16()
        _crc_enabled = r.u8()
        cmd_count = r.u8()
        _timestamp = r.u32()
        _challenge = r.i32()

        for _ in range(cmd_count):
            if r.pos >= len(payload):
                break
            try:
                cmd_type = r.u8()
                _channel = r.u8()
                _flags = r.u8()
                _reserved = r.u8()
                cmd_length = r.u32()
                _reliable_seq = r.u32()
            except IndexError:
                break
            data_len = cmd_length - 12
            if data_len < 0 or r.pos + data_len > len(payload):
                break
            data = payload[r.pos : r.pos + data_len]
            r.pos += data_len

            if cmd_type in (CMD_SEND_RELIABLE, CMD_SEND_UNRELIABLE):
                try:
                    messages.extend(_parse_message_block(data))
                except (IndexError, ValueError):
                    continue
            elif cmd_type == CMD_SEND_FRAGMENT and fragment_buffer is not None:
                # Format SendFragment (apres le command header de 12 bytes) :
                #   StartSequenceNumber : uint32 BE
                #   FragmentCount       : uint32 BE
                #   FragmentNumber      : uint32 BE
                #   TotalLength         : uint32 BE
                #   FragmentOffset      : uint32 BE
                #   Payload             : bytes (reste du data)
                if len(data) < 20:
                    continue
                try:
                    (
                        start_seq,
                        frag_count,
                        frag_num,
                        _total_length,
                        _frag_offset,
                    ) = struct.unpack_from(">IIIII", data, 0)
                except struct.error:
                    continue
                frag_payload = data[20:]
                assembled = fragment_buffer.add(
                    start_seq, frag_count, frag_num, frag_payload
                )
                if assembled is not None:
                    try:
                        messages.extend(_parse_message_block(assembled))
                    except (IndexError, ValueError):
                        continue
    except (IndexError, ValueError, struct.error):
        pass

    return messages


def _parse_message_block(data: bytes) -> list[PhotonMessage]:
    """Parse le contenu d'un command SendReliable/SendUnreliable qui contient
    un message Photon (event, op request, op response)."""
    if len(data) < 2:
        return []
    sig = data[0]
    if sig != 0xF3:
        return []
    msg_type = data[1] & 0x7F
    r = _Reader(data, pos=2)

    if msg_type == MSG_EVENT:
        code = r.u8()
        params = _read_param_table(r)
        return [PhotonMessage(kind="event", code=code, params=params)]
    if msg_type == MSG_OP_REQUEST:
        code = r.u8()
        params = _read_param_table(r)
        return [PhotonMessage(kind="op_request", code=code, params=params)]
    if msg_type in (MSG_RESULT, MSG_OP_RESPONSE):
        code = r.u8()
        ret = r.i16()
        dbg_type = r.u8()
        debug = _read_value(r, dbg_type) if dbg_type != 0x2A else None  # 0x2A = '*' = null-ish
        params = _read_param_table(r)
        return [
            PhotonMessage(
                kind="op_response",
                code=code,
                params=params,
                return_code=ret,
                debug_message=debug if isinstance(debug, str) else None,
            )
        ]
    return []
