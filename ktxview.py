#!/usr/bin/env python3
"""
ktxview.py  –  View KTX images directly in the terminal over SSH.

Usage:
    python ktxview.py image.ktx
    python ktxview.py image.ktx --width 60
    python ktxview.py image.ktx --method [auto|iterm2|kitty|blocks]

Display methods:
    auto   - detect terminal automatically (default)
    iterm2 - iTerm2 / Blink Shell inline image protocol
    kitty  - Kitty terminal graphics protocol
    blocks - Unicode half-block art  (works in any 24-bit color terminal)

Dependencies on the device:
    pip install lzfse texture2ddecoder
    (lzfse can also be resolved from the macOS/iOS system liblzfse.dylib)
    No Pillow required – PNG encoding done with stdlib zlib/struct.
"""

import argparse
import base64
import os
import shutil
import struct
import sys
import zlib

# ── Optional imports ──────────────────────────────────────────────────────────

def _load_lzfse():
    # 1) pip package
    try:
        import lzfse
        return lzfse.decompress
    except ImportError:
        pass
    # 2) system shared library (macOS / jailbroken iOS)
    try:
        import ctypes, ctypes.util
        _path = ctypes.util.find_library('lzfse') or 'liblzfse.dylib'
        _lib = ctypes.CDLL(_path)
        _lib.lzfse_decode_buffer.restype = ctypes.c_size_t
        _lib.lzfse_decode_buffer.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        def _decompress(data):
            src = bytes(data)
            out_size = max(len(src) * 10, 1 << 20)
            while True:
                out = ctypes.create_string_buffer(out_size)
                n = _lib.lzfse_decode_buffer(out, out_size, src, len(src), None)
                if n == 0:
                    raise RuntimeError("lzfse_decode_buffer failed (bad data or buffer too small)")
                if n < out_size:
                    return bytes(out[:n])
                out_size *= 4
        return _decompress
    except Exception:
        pass
    return None


def _load_astc():
    try:
        import texture2ddecoder
        return texture2ddecoder.decode_astc
    except ImportError:
        return None


_lzfse_decompress = _load_lzfse()
_decode_astc      = _load_astc()

# ── KTX decoding ──────────────────────────────────────────────────────────────

ASTC_FORMATS = {
    0x93B0:(4,4),  0x93B1:(5,4),  0x93B2:(5,5),  0x93B3:(6,5),
    0x93B4:(6,6),  0x93B5:(8,5),  0x93B6:(8,6),  0x93B7:(8,8),
    0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10),
    0x93BC:(12,10),0x93BD:(12,12),
    0x93D0:(4,4),  0x93D1:(5,4),  0x93D2:(5,5),  0x93D3:(6,5),
    0x93D4:(6,6),  0x93D5:(8,5),  0x93D6:(8,6),  0x93D7:(8,8),
    0x93D8:(10,5), 0x93D9:(10,6), 0x93DA:(10,8), 0x93DB:(10,10),
    0x93DC:(12,10),0x93DD:(12,12),
}


def decode_ktx_file(path):
    with open(path, 'rb') as f:
        data = f.read()

    magic = data[:8]

    if magic == b'AAPL\r\n\x1a\n':
        # Apple-variant KTX (LZFSE-compressed ASTC)
        gl_internal = struct.unpack_from('<I', data, 0x20)[0]
        width       = struct.unpack_from('<I', data, 0x28)[0]
        height      = struct.unpack_from('<I', data, 0x2C)[0]
        if gl_internal not in ASTC_FORMATS:
            raise ValueError(f"Unsupported glInternalFormat: 0x{gl_internal:04X}")
        bw, bh = ASTC_FORMATS[gl_internal]

        compressed_start = None
        for sig in (b'bvx2', b'bvx1', b'bvxn', b'bvx-'):
            pos = data.find(sig)
            if pos >= 0:
                compressed_start = pos
                break

        if compressed_start is not None:
            if _lzfse_decompress is None:
                raise ImportError(
                    "lzfse not available.\n"
                    "  Install: pip install lzfse\n"
                    "  On iOS:  the system liblzfse.dylib will be used automatically."
                )
            end_pos = data.rfind(b'END ')
            compressed = data[compressed_start:end_pos] if end_pos > compressed_start else data[compressed_start:]
            raw_astc = _lzfse_decompress(compressed)
        else:
            header_size = struct.unpack_from('<I', data, 8)[0]
            raw_astc = data[8 + header_size:]

    elif data[:12] == b'\xabKTX 11\xbb\r\n\x1a\n':
        # Standard KTX 1.0
        fmt = '<' if struct.unpack_from('<I', data, 12)[0] == 0x04030201 else '>'
        gl_internal = struct.unpack_from(f'{fmt}I', data, 28)[0]
        width       = struct.unpack_from(f'{fmt}I', data, 36)[0]
        height      = struct.unpack_from(f'{fmt}I', data, 40)[0]
        if gl_internal not in ASTC_FORMATS:
            raise ValueError(f"Unsupported glInternalFormat: 0x{gl_internal:04X}")
        bw, bh = ASTC_FORMATS[gl_internal]
        kv_bytes = struct.unpack_from(f'{fmt}I', data, 60)[0]
        off = 64 + kv_bytes
        image_size = struct.unpack_from(f'{fmt}I', data, off)[0]
        raw_astc = data[off + 4: off + 4 + image_size]

    else:
        raise ValueError("Not a recognized KTX file (expected AAPL or KTX1 magic).")

    if _decode_astc is None:
        raise ImportError("texture2ddecoder not available.  Install: pip install texture2ddecoder")

    # Returns raw BGRA bytes (B=0, G=1, R=2, A=3 per pixel)
    bgra = _decode_astc(raw_astc, width, height, bw, bh)

    info = {
        'width': width, 'height': height,
        'format': f'ASTC {bw}x{bh}',
        'ktype': 'Apple' if magic[:4] == b'AAPL' else 'Standard',
        'file_size': len(data),
    }
    return bgra, width, height, info


# ── Pure-Python image helpers ────────────────────────────────────────────────

def _resize_nn(bgra, src_w, src_h, dst_w, dst_h):
    """Nearest-neighbour resize of raw BGRA bytes. Returns bytes."""
    if not isinstance(bgra, (bytes, bytearray)):
        bgra = bytes(bgra)
    out = bytearray(dst_w * dst_h * 4)
    x_ratio = src_w / dst_w
    y_ratio = src_h / dst_h
    for y in range(dst_h):
        sy = int(y * y_ratio)
        src_row_off = sy * src_w * 4
        dst_row_off = y  * dst_w * 4
        for x in range(dst_w):
            sx = int(x * x_ratio)
            si = src_row_off + sx * 4
            di = dst_row_off + x  * 4
            out[di:di + 4] = bgra[si:si + 4]
    return bytes(out)


def _resize_area(bgra, src_w, src_h, dst_w, dst_h):
    """
    Area-averaging (box filter) resize.  Each destination pixel is the average
    of all source pixels it covers.  Much better quality than nearest-neighbour
    for large downscale factors.  Uses list-comprehension row accumulation for
    reasonable pure-Python performance.
    """
    if not isinstance(bgra, (bytes, bytearray)):
        bgra = bytes(bgra)
    x_ratio = src_w / dst_w
    y_ratio = src_h / dst_h
    out = bytearray(dst_w * dst_h * 4)

    for dy in range(dst_h):
        sy0 = int(dy * y_ratio)
        sy1 = min(int((dy + 1) * y_ratio) + 1, src_h)
        cnt_y = sy1 - sy0

        # Accumulate source rows channel-by-channel using C-speed slice ops
        acc_b = [0] * src_w
        acc_g = [0] * src_w
        acc_r = [0] * src_w
        acc_a = [0] * src_w
        for sy in range(sy0, sy1):
            row = bgra[sy * src_w * 4: (sy + 1) * src_w * 4]
            acc_b = [x + y for x, y in zip(acc_b, row[0::4])]
            acc_g = [x + y for x, y in zip(acc_g, row[1::4])]
            acc_r = [x + y for x, y in zip(acc_r, row[2::4])]
            acc_a = [x + y for x, y in zip(acc_a, row[3::4])]

        # Reduce horizontally for each destination column
        off = dy * dst_w * 4
        for dx in range(dst_w):
            sx0 = int(dx * x_ratio)
            sx1 = min(int((dx + 1) * x_ratio) + 1, src_w)
            cnt = cnt_y * (sx1 - sx0)
            di = off + dx * 4
            out[di]     = sum(acc_b[sx0:sx1]) // cnt
            out[di + 1] = sum(acc_g[sx0:sx1]) // cnt
            out[di + 2] = sum(acc_r[sx0:sx1]) // cnt
            out[di + 3] = sum(acc_a[sx0:sx1]) // cnt

    return bytes(out)


def _bgra_to_rgba(bgra):
    """Swap B and R channels using C-speed bytearray slice assignment."""
    if not isinstance(bgra, (bytes, bytearray)):
        bgra = bytes(bgra)
    a = bytearray(len(bgra))
    a[0::4] = bgra[2::4]   # R ← B
    a[1::4] = bgra[1::4]   # G unchanged
    a[2::4] = bgra[0::4]   # B ← R
    a[3::4] = bgra[3::4]   # A unchanged
    return bytes(a)


def _png_encode(rgba, width, height):
    """Encode raw RGBA bytes as a PNG using only stdlib (zlib + struct)."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack('>II5B', width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    row_len = width * 4
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # filter: None
        scanlines += rgba[y * row_len:(y + 1) * row_len]
    idat = zlib.compress(bytes(scanlines), 6)

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', idat)
        + chunk(b'IEND', b'')
    )


# ── Terminal detection ────────────────────────────────────────────────────────

def detect_method():
    term      = os.environ.get('TERM', '')
    term_prog = os.environ.get('TERM_PROGRAM', '')
    lc_term   = os.environ.get('LC_TERMINAL', '')

    if term == 'xterm-kitty':
        return 'kitty'
    if term_prog == 'iTerm.app' or lc_term == 'iTerm2':
        return 'iterm2'
    # Blink Shell (iOS) supports iTerm2 inline image protocol
    if any(k in os.environ for k in ('BLINK_TERMINAL', 'BLINK')):
        return 'iterm2'
    # If we have 24-bit color, blocks look great
    return 'blocks'


# ── Renderers ─────────────────────────────────────────────────────────────────

def render_iterm2(bgra, width, height, term_cols):
    """
    iTerm2 / Blink Shell inline image protocol (OSC 1337).
    Image is pre-scaled to term_cols*4 pixels wide so the PNG payload stays
    small enough to transfer reliably over SSH.
    """
    # Keep payload small: target 4 px per column (looks sharp on Retina)
    max_px = term_cols * 4
    if width > max_px:
        target_h = max(1, round(height * max_px / width))
        bgra = _resize_nn(bgra, width, height, max_px, target_h)
        width, height = max_px, target_h
    rgba = _bgra_to_rgba(bgra)
    png  = _png_encode(rgba, width, height)
    b64  = base64.b64encode(png).decode('ascii')
    payload = f'\033]1337;File=inline=1;width={term_cols};preserveAspectRatio=1:{b64}\a\n'
    sys.stdout.buffer.write(payload.encode('ascii'))
    sys.stdout.buffer.flush()


def render_kitty(bgra, width, height, term_cols):
    """
    Kitty terminal graphics protocol – chunked base64 PNG.
    https://sw.kovidgoyal.net/kitty/graphics-protocol/
    Sends PNG (f=100) instead of raw RGBA: 10-50x less data over SSH.
    q=2 suppresses terminal acknowledgement responses.
    """
    # Scale to fit terminal: 4 px per column matches iTerm2 pre-scale
    max_px = term_cols * 4
    if width > max_px:
        target_h = max(1, round(height * max_px / width))
        bgra = _resize_nn(bgra, width, height, max_px, target_h)
        width, height = max_px, target_h
    rgba    = _bgra_to_rgba(bgra)
    png     = _png_encode(rgba, width, height)

    encoded = base64.standard_b64encode(png).decode('ascii')
    CHUNK   = 4096
    parts   = [encoded[i:i + CHUNK] for i in range(0, len(encoded), CHUNK)]

    for i, part in enumerate(parts):
        m = 1 if i < len(parts) - 1 else 0
        if i == 0:
            # f=100: PNG data; c=columns: display width; q=2: no ACK
            ctrl = f'a=T,f=100,c={term_cols},q=2,m={m}'
        else:
            ctrl = f'm={m}'  # continuation chunks only need m=
        sys.stdout.buffer.write(f'\033_G{ctrl};{part}\033\\'.encode('ascii'))

    sys.stdout.buffer.write(b'\n')
    sys.stdout.buffer.flush()


def render_blocks(bgra, width, height, term_cols):
    """
    Unicode half-block art with 24-bit ANSI colour.
    '▀' upper-half fg = top pixel, bg = bottom pixel → 2 image rows per line.
    Uses area-averaging for best visual quality at low character resolution.
    """
    target_w = min(width, term_cols)
    scale    = target_w / width
    target_h = max(2, round(height * scale * 0.5) * 2)  # keep even; *0.5 for 2:1 cell ratio

    resized  = _resize_nn(bgra, width, height, target_w, target_h)
    row_size = target_w * 4

    lines = []
    for row in range(0, target_h - 1, 2):
        off1 = row       * row_size
        off2 = (row + 1) * row_size
        row_chars = []
        for col in range(target_w):
            i1 = off1 + col * 4
            i2 = off2 + col * 4
            b1, g1, r1, a1 = resized[i1], resized[i1+1], resized[i1+2], resized[i1+3]
            b2, g2, r2, a2 = resized[i2], resized[i2+1], resized[i2+2], resized[i2+3]
            t_trans = a1 < 128
            b_trans = a2 < 128

            if t_trans and b_trans:
                row_chars.append('\033[0m ')
            elif t_trans:
                row_chars.append(f'\033[0;38;2;{r2};{g2};{b2}m\033[49m\u2584')
            elif b_trans:
                row_chars.append(f'\033[0;38;2;{r1};{g1};{b1}m\033[49m\u2580')
            else:
                row_chars.append(
                    f'\033[38;2;{r1};{g1};{b1}m'
                    f'\033[48;2;{r2};{g2};{b2}m\u2580'
                )
        row_chars.append('\033[0m')
        lines.append(''.join(row_chars))

    sys.stdout.buffer.write(('\n'.join(lines) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='View KTX images in the SSH terminal.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('file', help='Path to .ktx file')
    parser.add_argument(
        '--width', type=int, default=0,
        help='Display width in columns (default: auto-detect terminal width)',
    )
    parser.add_argument(
        '--method', choices=['auto', 'iterm2', 'kitty', 'blocks'],
        default='auto',
        help='Rendering method (default: auto)',
    )
    args = parser.parse_args()

    term_cols = args.width or shutil.get_terminal_size((80, 24)).columns
    method    = detect_method() if args.method == 'auto' else args.method

    try:
        bgra, width, height, info = decode_ktx_file(args.file)
    except Exception as e:
        print(f"\033[31mError:\033[0m {e}", file=sys.stderr)
        sys.exit(1)

    name = os.path.basename(args.file)
    print(
        f"\033[1m{name}\033[0m  "
        f"{info['width']}×{info['height']}  "
        f"{info['format']}  "
        f"[{info['ktype']} KTX  {info['file_size']:,} bytes]  "
        f"method: {method}"
    )

    if method == 'iterm2':
        render_iterm2(bgra, width, height, term_cols)
    elif method == 'kitty':
        render_kitty(bgra, width, height, term_cols)
    else:
        render_blocks(bgra, width, height, term_cols)


if __name__ == '__main__':
    main()
