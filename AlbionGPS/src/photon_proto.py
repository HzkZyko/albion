"""
Decodeur du protocole Photon (ExitGames) pour Albion Online.

Supporte Protocol18 (depuis la mise a jour "Radiant Wilds" du 13/04/2026) :
- Serialisation Protocol18 avec types compresses (varint), endianness LE
- Nouveaux codes de type (0..34 + 0x40+ tableaux + 0x80+ custom slim)
- Compression varint (LEB128 zigzag) pour int32 et int64

Port Python du deserialiseur Go de albiondata-client, lui-meme porte du
C# Protocol18Deserializer de JPCodeCraft/AlbionDataAvalonia.

Reference : https://github.com/ao-data/albiondata-client
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Optional


# ---- Photon protocol constants ---------------------------------------------

# Command types (inchanges entre les versions du protocole)
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
MSG_ENCRYPTED = 131  # 0x83 - message chiffre (marche par ex.)


# ---- Protocol18 type codes -------------------------------------------------

P18_UNKNOWN = 0
P18_BOOLEAN = 2
P18_BYTE = 3
P18_SHORT = 4
P18_FLOAT = 5
P18_DOUBLE = 6
P18_STRING = 7
P18_NULL = 8
P18_COMPRESSED_INT = 9
P18_COMPRESSED_LONG = 10
P18_INT1 = 11
P18_INT1_NEG = 12
P18_INT2 = 13
P18_INT2_NEG = 14
P18_LONG1 = 15
P18_LONG1_NEG = 16
P18_LONG2 = 17
P18_LONG2_NEG = 18
P18_CUSTOM = 19
P18_DICTIONARY = 20
P18_HASHTABLE = 21
# 22 = unused
P18_OBJECT_ARRAY = 23
P18_OP_REQUEST = 24
P18_OP_RESPONSE = 25
P18_EVENT_DATA = 26
P18_BOOL_FALSE = 27
P18_BOOL_TRUE = 28
P18_SHORT_ZERO = 29
P18_INT_ZERO = 30
P18_LONG_ZERO = 31
P18_FLOAT_ZERO = 32
P18_DOUBLE_ZERO = 33
P18_BYTE_ZERO = 34
P18_ARRAY = 0x40  # bare array; 0x40|elemType = typed array
P18_CUSTOM_SLIM_BASE = 0x80  # >= 0x80: slim custom type


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

    Utilise fragOffset pour positionner les donnees correctement (comme
    le fait albiondata-client) plutot que de concatener par frag_num.
    """

    MAX_PENDING_GROUPS = 128

    def __init__(self) -> None:
        # start_seq -> {"total": int, "written": int, "payload": bytearray}
        self._groups: dict[int, dict] = {}
        self._order: list[int] = []
        self.fragments_received = 0
        self.groups_assembled = 0
        self.last_assembled: list[bytes] = []

    def add(
        self,
        start_seq: int,
        total_length: int,
        frag_offset: int,
        frag_data: bytes,
    ) -> Optional[bytes]:
        """Ajoute un fragment. Retourne les bytes reassembles si le groupe
        est complet, sinon None."""
        self.fragments_received += 1
        if total_length <= 0 or total_length > 500_000:
            return None

        if start_seq not in self._groups:
            if len(self._order) >= self.MAX_PENDING_GROUPS:
                evict = self._order.pop(0)
                self._groups.pop(evict, None)
            self._groups[start_seq] = {
                "total": total_length,
                "written": 0,
                "payload": bytearray(total_length),
            }
            self._order.append(start_seq)

        seg = self._groups[start_seq]
        end = frag_offset + len(frag_data)
        if end <= seg["total"]:
            seg["payload"][frag_offset:end] = frag_data
        seg["written"] += len(frag_data)

        if seg["written"] >= seg["total"]:
            assembled = bytes(seg["payload"])
            self._groups.pop(start_seq, None)
            try:
                self._order.remove(start_seq)
            except ValueError:
                pass
            self.groups_assembled += 1
            self.last_assembled.append(assembled)
            return assembled
        return None

    def clear(self) -> None:
        self._groups.clear()
        self._order.clear()
        self.last_assembled.clear()


# ---- Protocol18 Reader (Little Endian) -------------------------------------


class _P18Reader:
    """Curseur binaire pour Protocol18. Les valeurs multi-octets sont en
    Little Endian (contrairement a l'ancien protocole qui etait Big Endian).
    L'en-tete du paquet Photon reste en Big Endian."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def remaining(self) -> int:
        return len(self.buf) - self.pos

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise IndexError("p18 reader underrun")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.read(1)[0]

    def i16_le(self) -> int:
        return struct.unpack("<h", self.read(2))[0]

    def u16_le(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def f32_le(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def f64_le(self) -> float:
        return struct.unpack("<d", self.read(8))[0]

    def compressed_uint32(self) -> int:
        """Lit un varint non-signe (LEB128)."""
        value = 0
        shift = 0
        while True:
            b = self.u8()
            value |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                return value
            shift += 7
            if shift >= 35:
                return 0

    def compressed_int32(self) -> int:
        """Lit un varint signe (zigzag + LEB128)."""
        v = self.compressed_uint32()
        return (v >> 1) ^ (-(v & 1))

    def compressed_uint64(self) -> int:
        value = 0
        shift = 0
        while True:
            b = self.u8()
            value |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                return value
            shift += 7
            if shift >= 70:
                return 0

    def compressed_int64(self) -> int:
        v = self.compressed_uint64()
        return (v >> 1) ^ (-(v & 1))

    def p18_string(self) -> str:
        """Lit un string Protocol18 : varint longueur + bytes UTF-8."""
        length = self.compressed_uint32()
        if length <= 0 or self.pos + length > len(self.buf):
            return ""
        data = self.read(length)
        return data.decode("utf-8", errors="replace")

    def p18_count(self) -> int:
        """Lit le prefixe de taille d'une collection (varint)."""
        return self.compressed_uint32()


# ---- Protocol18 deserializer -----------------------------------------------


def _p18_deserialize(r: _P18Reader, tc: int) -> Any:
    """Deserialise une valeur Protocol18 selon son code de type."""
    if tc >= P18_CUSTOM_SLIM_BASE:
        return _p18_deserialize_custom(r, tc)
    if tc == P18_UNKNOWN or tc == P18_NULL:
        return None
    if tc == P18_BOOLEAN:
        return r.u8() != 0
    if tc == P18_BYTE:
        return r.u8()
    if tc == P18_SHORT:
        return r.i16_le()
    if tc == P18_FLOAT:
        return r.f32_le()
    if tc == P18_DOUBLE:
        return r.f64_le()
    if tc == P18_STRING:
        return r.p18_string()
    if tc == P18_COMPRESSED_INT:
        return r.compressed_int32()
    if tc == P18_COMPRESSED_LONG:
        return r.compressed_int64()
    if tc == P18_INT1:
        return r.u8()
    if tc == P18_INT1_NEG:
        return -r.u8()
    if tc == P18_INT2:
        return r.u16_le()
    if tc == P18_INT2_NEG:
        return -r.u16_le()
    if tc == P18_LONG1:
        return r.u8()
    if tc == P18_LONG1_NEG:
        return -r.u8()
    if tc == P18_LONG2:
        return r.u16_le()
    if tc == P18_LONG2_NEG:
        return -r.u16_le()
    if tc == P18_CUSTOM:
        return _p18_deserialize_custom(r, 0)
    if tc == P18_DICTIONARY:
        return _p18_deserialize_dictionary(r)
    if tc == P18_HASHTABLE:
        return _p18_deserialize_dictionary(r)
    if tc == P18_OBJECT_ARRAY:
        return _p18_deserialize_object_array(r)
    if tc == P18_OP_REQUEST:
        return _p18_deserialize_op_request_inner(r)
    if tc == P18_OP_RESPONSE:
        return _p18_deserialize_op_response_inner(r)
    if tc == P18_EVENT_DATA:
        return _p18_deserialize_event_inner(r)
    if tc == P18_BOOL_FALSE:
        return False
    if tc == P18_BOOL_TRUE:
        return True
    if tc == P18_SHORT_ZERO:
        return 0
    if tc == P18_INT_ZERO:
        return 0
    if tc == P18_LONG_ZERO:
        return 0
    if tc == P18_FLOAT_ZERO:
        return 0.0
    if tc == P18_DOUBLE_ZERO:
        return 0.0
    if tc == P18_BYTE_ZERO:
        return 0
    if tc == P18_ARRAY:
        return _p18_deserialize_nested_array(r)
    if tc & P18_ARRAY == P18_ARRAY:
        return _p18_deserialize_typed_array(r, tc & ~P18_ARRAY)
    raise ValueError(f"unknown p18 type 0x{tc:02x}")


def _p18_deserialize_typed_array(r: _P18Reader, elem_type: int) -> Any:
    size = r.p18_count()
    if size > 100_000:
        return []
    if elem_type == P18_BYTE:
        return r.read(size)
    if elem_type == P18_STRING:
        return [r.p18_string() for _ in range(size)]
    if elem_type == P18_COMPRESSED_INT:
        return [r.compressed_int32() for _ in range(size)]
    if elem_type == P18_COMPRESSED_LONG:
        return [r.compressed_int64() for _ in range(size)]
    if elem_type == P18_SHORT:
        return [r.i16_le() for _ in range(size)]
    if elem_type == P18_FLOAT:
        return [r.f32_le() for _ in range(size)]
    if elem_type == P18_DOUBLE:
        return [r.f64_le() for _ in range(size)]
    if elem_type == P18_BOOLEAN:
        packed_bytes = (size + 7) // 8
        packed = r.read(packed_bytes)
        return [(packed[i // 8] & (1 << (i % 8))) != 0 for i in range(size)]
    return [_p18_deserialize(r, elem_type) for _ in range(size)]


def _p18_deserialize_nested_array(r: _P18Reader) -> Any:
    size = r.p18_count()
    tc = r.u8()
    return [_p18_deserialize(r, tc) for _ in range(size)]


def _p18_deserialize_object_array(r: _P18Reader) -> list:
    size = r.p18_count()
    result = []
    for _ in range(size):
        tc = r.u8()
        result.append(_p18_deserialize(r, tc))
    return result


def _p18_deserialize_dictionary(r: _P18Reader) -> dict:
    key_tc = r.u8()
    val_tc = r.u8()
    count = r.p18_count()
    out: dict = {}
    for i in range(count):
        if r.remaining() <= 0:
            break
        kt = key_tc if key_tc != 0 else r.u8()
        vt = val_tc if val_tc != 0 else r.u8()
        k = _p18_deserialize(r, kt)
        v = _p18_deserialize(r, vt)
        try:
            out[k] = v
        except TypeError:
            out[f"unhashable_{i}"] = v
    return out


def _p18_deserialize_custom(r: _P18Reader, gp_type: int) -> Any:
    is_slim = gp_type >= P18_CUSTOM_SLIM_BASE
    custom_id = (gp_type & 0x7F) if is_slim else r.u8()
    size = r.p18_count()
    if size < 0 or size > r.remaining():
        data = r.read(r.remaining())
    else:
        data = r.read(size)
    return {"custom_type": custom_id, "data": data}


def _p18_deserialize_op_request_inner(r: _P18Reader) -> dict:
    op_code = r.u8()
    params = _p18_read_param_table(r)
    return {"op_request": op_code, "params": params}


def _p18_deserialize_op_response_inner(r: _P18Reader) -> dict:
    op_code = r.u8()
    ret_code = r.i16_le()
    debug_msg = ""
    if r.remaining() > 0:
        tc = r.u8()
        val = _p18_deserialize(r, tc)
        if isinstance(val, str):
            debug_msg = val
    params = _p18_read_param_table(r)
    return {"op_response": op_code, "return_code": ret_code,
            "debug": debug_msg, "params": params}


def _p18_deserialize_event_inner(r: _P18Reader) -> dict:
    code = r.u8()
    params = _p18_read_param_table(r)
    return {"event": code, "params": params}


def _p18_read_param_table(r: _P18Reader) -> dict[int, Any]:
    """Lit une table de parametres Protocol18 : varint count, puis
    (key_byte, type_byte, value) * count."""
    count = r.p18_count()
    out: dict[int, Any] = {}
    for _ in range(count):
        if r.remaining() < 2:
            break
        key = r.u8()
        tc = r.u8()
        try:
            out[key] = _p18_deserialize(r, tc)
        except (ValueError, IndexError):
            break
    return out


# ---- Top-level packet parser ------------------------------------------------


def parse_photon_packet(
    payload: bytes,
    fragment_buffer: Optional[FragmentBuffer] = None,
) -> list[PhotonMessage]:
    """Decode un paquet Photon et retourne la liste des messages applicatifs.

    Supporte Protocol18 (Radiant Wilds, avril 2026).
    L'en-tete du paquet reste en Big Endian, mais le contenu des messages
    utilise la serialisation Protocol18 (Little Endian, varint, etc.).
    """

    messages: list[PhotonMessage] = []
    if len(payload) < 12:
        return messages

    try:
        # Header du paquet Photon (toujours Big Endian)
        _peer_id = struct.unpack_from(">H", payload, 0)[0]
        flags = payload[2]
        cmd_count = payload[3]
        # timestamp (4 bytes) + challenge (4 bytes) = skip

        # flags == 1 signifie que le paquet entier est chiffre
        if flags == 1:
            return messages

        pos = 12  # apres le header de 12 bytes

        for _ in range(cmd_count):
            if pos + 12 > len(payload):
                break
            cmd_type = payload[pos]
            # channel = payload[pos + 1]
            # cmd_flags = payload[pos + 2]
            # reserved = payload[pos + 3]
            cmd_length = struct.unpack_from(">I", payload, pos + 4)[0]
            # reliable_seq = struct.unpack_from(">I", payload, pos + 8)[0]
            pos += 12

            data_len = cmd_length - 12
            if data_len < 0 or pos + data_len > len(payload):
                break
            data = payload[pos : pos + data_len]
            pos += data_len

            if cmd_type == CMD_SEND_RELIABLE:
                try:
                    messages.extend(_parse_message_block_p18(data))
                except (IndexError, ValueError):
                    continue

            elif cmd_type == CMD_SEND_UNRELIABLE:
                # 4 bytes de sequence number avant le message
                if len(data) > 4:
                    try:
                        messages.extend(_parse_message_block_p18(data[4:]))
                    except (IndexError, ValueError):
                        continue

            elif cmd_type == CMD_SEND_FRAGMENT and fragment_buffer is not None:
                if len(data) < 20:
                    continue
                try:
                    (
                        start_seq,
                        _frag_count,
                        _frag_num,
                        total_length,
                        frag_offset,
                    ) = struct.unpack_from(">IIIII", data, 0)
                except struct.error:
                    continue
                frag_payload = data[20:]
                assembled = fragment_buffer.add(
                    start_seq, total_length, frag_offset, frag_payload
                )
                if assembled is not None:
                    try:
                        messages.extend(_parse_message_block_p18(assembled))
                    except (IndexError, ValueError):
                        continue
            # else: ACK, PING, CONNECT, etc. - on ignore

    except (IndexError, ValueError, struct.error):
        pass

    return messages


def _parse_message_block_p18(data: bytes) -> list[PhotonMessage]:
    """Parse un bloc de message Photon avec Protocol18.

    Format : signal_byte (ignore) | msg_type | contenu
    Le signal_byte est lu mais ignore (comme dans albiondata-client).
    """
    if len(data) < 2:
        return []

    # signal_byte = data[0]  # ignore (etait 0xF3 avant, peut varier)
    msg_type = data[1]

    # Message chiffre (ex. donnees de marche)
    if msg_type == MSG_ENCRYPTED:
        return []

    r = _P18Reader(data, pos=2)

    if msg_type == MSG_EVENT:
        code = r.u8()
        params = _p18_read_param_table(r)
        return [PhotonMessage(kind="event", code=code, params=params)]

    if msg_type == MSG_OP_REQUEST:
        code = r.u8()
        params = _p18_read_param_table(r)
        return [PhotonMessage(kind="op_request", code=code, params=params)]

    if msg_type in (MSG_RESULT, MSG_OP_RESPONSE):
        code = r.u8()
        ret_code = r.i16_le()
        # Debug message : un type byte + valeur Protocol18
        debug_msg = None
        if r.remaining() > 0:
            dbg_tc = r.u8()
            dbg_val = _p18_deserialize(r, dbg_tc)
            if isinstance(dbg_val, str):
                debug_msg = dbg_val
        params = _p18_read_param_table(r)
        return [
            PhotonMessage(
                kind="op_response",
                code=code,
                params=params,
                return_code=ret_code,
                debug_message=debug_msg,
            )
        ]

    return []
