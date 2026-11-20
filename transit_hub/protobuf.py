import struct


class DecodeError(ValueError):
    pass


def read_varint(buf, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise DecodeError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def signed(value):
    return value - (1 << 64) if value >= 1 << 63 else value


def fields(buf):
    """Yield (field_number, wire_type, value) for each field of one encoded message."""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = read_varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            stop = pos + length
            if stop > end:
                raise DecodeError("length-delimited field runs past the message")
            value = buf[pos:stop]
            pos = stop
        elif wire == 1:
            if pos + 8 > end:
                raise DecodeError("truncated fixed64")
            value = struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        elif wire == 5:
            if pos + 4 > end:
                raise DecodeError("truncated fixed32")
            value = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise DecodeError("unsupported wire type %d" % wire)
        yield number, wire, value
