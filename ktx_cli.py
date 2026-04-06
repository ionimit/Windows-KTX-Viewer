#!/usr/bin/env python3
"""
ktx_cli.py  –  Command-line KTX image extractor

Export KTX textures to PNG / JPEG from a local path or an SSH-remote server.
The exported image includes a macOS-style title bar showing the original
filename, making it look like a screenshot taken from an image viewer.

Dependencies:
    pip install lzfse texture2ddecoder Pillow paramiko
"""

import io
import os
import struct
import sys
import textwrap
import urllib.parse

# ── Optional dependencies ─────────────────────────────────────────────────────

try:
    import lzfse
except ImportError:
    lzfse = None  # type: ignore

try:
    import texture2ddecoder
except ImportError:
    texture2ddecoder = None  # type: ignore

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = ImageDraw = ImageFont = ImageFilter = None  # type: ignore

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    paramiko = None  # type: ignore

# ── KTX Parsing ───────────────────────────────────────────────────────────────

APPLE_KTX_MAGIC = b'AAPL\r\n\x1a\n'
STD_KTX1_MAGIC  = b'\xabKTX 11\xbb\r\n\x1a\n'

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


def _decode_ktx_bytes(data: bytes):
    """Decode raw KTX file bytes → (PIL.Image, info_dict)."""
    magic = data[:8]

    if magic == APPLE_KTX_MAGIC:
        gl_internal = struct.unpack_from('<I', data, 0x20)[0]
        width       = struct.unpack_from('<I', data, 0x28)[0]
        height      = struct.unpack_from('<I', data, 0x2C)[0]
        if gl_internal not in ASTC_FORMATS:
            raise ValueError(f'Unsupported glInternalFormat: 0x{gl_internal:04X}')
        bw, bh = ASTC_FORMATS[gl_internal]
        compressed_start = None
        for sig in (b'bvx2', b'bvx1', b'bvxn', b'bvx-'):
            pos = data.find(sig)
            if pos >= 0:
                compressed_start = pos
                break
        if compressed_start is not None:
            if lzfse is None:
                raise ImportError('lzfse required: pip install lzfse')
            end_pos = data.rfind(b'END ')
            compressed = (data[compressed_start:end_pos]
                          if end_pos > compressed_start else data[compressed_start:])
            raw_astc = lzfse.decompress(compressed)
        else:
            header_size = struct.unpack_from('<I', data, 8)[0]
            raw_astc = data[8 + header_size:]
        ktype = 'Apple'

    elif data[:12] == STD_KTX1_MAGIC:
        fmt = '<' if struct.unpack_from('<I', data, 12)[0] == 0x04030201 else '>'
        gl_internal = struct.unpack_from(f'{fmt}I', data, 28)[0]
        width       = struct.unpack_from(f'{fmt}I', data, 36)[0]
        height      = struct.unpack_from(f'{fmt}I', data, 40)[0]
        if gl_internal not in ASTC_FORMATS:
            raise ValueError(f'Unsupported glInternalFormat: 0x{gl_internal:04X}')
        bw, bh = ASTC_FORMATS[gl_internal]
        kv_bytes = struct.unpack_from(f'{fmt}I', data, 60)[0]
        off = 64 + kv_bytes
        image_size = struct.unpack_from(f'{fmt}I', data, off)[0]
        raw_astc = data[off + 4: off + 4 + image_size]
        ktype = 'Standard'

    else:
        raise ValueError('Not a recognized KTX file (expected AAPL or KTX1 magic).')

    if texture2ddecoder is None:
        raise ImportError('texture2ddecoder required: pip install texture2ddecoder')

    rgba_raw = texture2ddecoder.decode_astc(raw_astc, width, height, bw, bh)
    img = Image.frombuffer('RGBA', (width, height), rgba_raw, 'raw', 'BGRA')

    info = {
        'type':      ktype,
        'width':     width,
        'height':    height,
        'format':    f'ASTC {bw}x{bh}',
        'gl':        f'0x{gl_internal:04X}',
        'raw_size':  len(raw_astc),
        'file_size': len(data),
    }
    return img, info


# ── Title-bar overlay ─────────────────────────────────────────────────────────

# Genuine macOS dark title bar – narrow, no gradients, flat dots
_TITLE_H    = 38               # px – compact like a real macOS title bar
_TITLE_BG   = (50, 50, 50)    # #323232
_TOP_RIM    = (82, 82, 82)    # 1-px top edge highlight
_SEP_SHADOW = (12, 12, 12)    # hard 1-px shadow below bar
_SEP_SOFT   = (32, 32, 32)    # soft 1-px transition below shadow

# macOS traffic-light exact colours (from Ventura/Sonoma)
_DOT_COLORS = [
    (255,  95,  87),   # close  – red     #FF5F57
    (255, 189,  46),   # min    – yellow  #FFBD2E
    ( 39, 201,  63),   # zoom   – green   #27C93F
]
_DOT_R   = 7    # dot radius (px)
_DOT_X0  = 16   # x-centre of leftmost dot
_DOT_GAP = 26   # centre-to-centre (increased spacing)

_TEXT_FG     = (220, 220, 220, 255)
_TEXT_SHADOW = (0, 0, 0, 150)
_TEXT_SIZE   = 23


def _load_font(size: int):
    candidates = [
        r'C:/Windows/Fonts/segoeuisl.ttf',
        r'C:/Windows/Fonts/segoeui.ttf',
        r'C:/Windows/Fonts/arial.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/SFNS.ttf',
        '/Library/Fonts/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/TTF/DejaVuSans.ttf',
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def add_title_bar(img, title: str):
    """
    Return a new RGBA image with a macOS dark title bar prepended.
      • Narrow flat bar (no gradient)
      • Flat traffic-light dots matching genuine macOS colours
      • Filename centred, 23 px, with a 1-px drop shadow
      • Two separator lines (hard shadow + soft) at the bottom of the bar
    """
    W, H = img.size
    bar_bottom = _TITLE_H + 2   # bar height + 2 separator px

    canvas = Image.new('RGBA', (W, H + bar_bottom), _TITLE_BG + (255,))
    draw = ImageDraw.Draw(canvas)

    # Flat solid background (no gradient)
    draw.rectangle([0, 0, W - 1, _TITLE_H - 1], fill=_TITLE_BG + (255,))

    # 1-px top-edge highlight (window chrome border)
    draw.line([0, 0, W - 1, 0], fill=_TOP_RIM + (255,), width=1)

    # ── Traffic-light dots with soft blur on edges ───────────────────────
    # Draw dots on a separate transparent layer, blur, then composite
    dots_layer = Image.new('RGBA', (W, _TITLE_H), (0, 0, 0, 0))
    dots_draw = ImageDraw.Draw(dots_layer)
    dot_cy = _TITLE_H // 2
    for i, base_rgb in enumerate(_DOT_COLORS):
        cx = _DOT_X0 + i * _DOT_GAP
        dots_draw.ellipse(
            [cx - _DOT_R, dot_cy - _DOT_R, cx + _DOT_R, dot_cy + _DOT_R],
            fill=base_rgb + (255,),
        )
    # Apply Gaussian blur to soften dot edges
    dots_layer = dots_layer.filter(ImageFilter.GaussianBlur(radius=0.6))
    canvas.alpha_composite(dots_layer, (0, 0))

    # ── Filename centred with 1-px drop-shadow ───────────────────────────
    font = _load_font(_TEXT_SIZE)
    try:
        bbox = draw.textbbox((0, 0), title, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (W - tw) // 2
        ty = (_TITLE_H - th) // 2 - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(title, font=font)   # type: ignore[attr-defined]
        tx = (W - tw) // 2
        ty = (_TITLE_H - th) // 2
    draw.text((tx + 1, ty + 1), title, fill=_TEXT_SHADOW, font=font)
    draw.text((tx,     ty    ), title, fill=_TEXT_FG,     font=font)

    # ── Two separator lines ──────────────────────────────────────────────
    draw.line([0, _TITLE_H,     W - 1, _TITLE_H    ], fill=_SEP_SHADOW + (255,), width=1)
    draw.line([0, _TITLE_H + 1, W - 1, _TITLE_H + 1], fill=_SEP_SOFT   + (255,), width=1)

    # ── Paste original image below the bar ───────────────────────────────
    canvas.paste(img, (0, bar_bottom))
    return canvas


# ── SSH file fetching ──────────────────────────────────────────────────────────

def _parse_ssh_url(url: str):
    """Parse  ssh://user:pass@host:port/path  → dict, or None if not an SSH URL."""
    p = urllib.parse.urlparse(url)
    if p.scheme.lower() != 'ssh':
        return None
    return {
        'host':     p.hostname or '',
        'port':     p.port or 22,
        'username': p.username or 'root',
        'password': urllib.parse.unquote(p.password or 'alpine'),
        'remote':   p.path,
    }


def fetch_remote(host: str, port: int, username: str, password: str,
                 key_path: str, remote_path: str) -> bytes:
    """Fetch a remote file over SSH/SFTP and return its raw bytes."""
    if not HAS_PARAMIKO:
        raise ImportError('paramiko is not available.  Install: pip install paramiko')

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw: dict = dict(
        hostname=host, port=port, username=username,
        timeout=15, look_for_keys=False, allow_agent=False,
    )
    if key_path and os.path.isfile(key_path):
        kw['key_filename'] = key_path
    else:
        kw['password'] = password
    client.connect(**kw)
    try:
        # Try SFTP first (preferred – binary-safe)
        try:
            sftp = client.open_sftp()
            try:
                buf = io.BytesIO()
                sftp.getfo(remote_path, buf)
                return buf.getvalue()
            except IOError:
                pass  # fall through to exec_command / cat
            finally:
                sftp.close()
        except Exception:
            pass  # SFTP subsystem unavailable → fall through

        # Fallback: stream file via  cat  (runs as the SSH-auth user, so root
        # can read files the SFTP sub-system cannot on iOS/macOS sandboxes).
        safe_path = remote_path.replace("'", "'\\''")
        _, stdout, stderr = client.exec_command(f"cat '{safe_path}'")
        data = stdout.read()
        err  = stderr.read().decode(errors='replace').strip()
        if not data and err:
            raise RuntimeError(f'Remote read failed: {err}')
        return data
    finally:
        client.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

_EPILOG = textwrap.dedent("""\
    examples:
      # Local file – PNG saved next to the source file
      ktx_cli image.ktx

      # Local file – custom output path
      ktx_cli splash.ktx -o C:/Users/me/Desktop/splash.png

      # SSH with default credentials  (root / alpine)
      ktx_cli --host 192.168.1.100 \\
              --remote /var/containers/Bundle/Application/.../splash.ktx

      # SSH with explicit credentials
      ktx_cli --host 192.168.1.100 --user mobile --password hunter2 \\
              --remote /var/mobile/splash.ktx -o splash.png

      # SSH with private key  (no password needed)
      ktx_cli --host 192.168.1.100 --key ~/.ssh/id_rsa \\
              --remote /private/var/containers/.../splash.ktx

      # SSH URL shorthand  (credentials embedded in URL)
      ktx_cli "ssh://root:alpine@192.168.1.100/var/containers/.../splash.ktx"

      # Export as JPEG  (alpha channel is composited on white automatically)
      ktx_cli image.ktx -o image.jpg

      # Export without the title-bar overlay
      ktx_cli image.ktx --no-title

      # Show file metadata only, without exporting
      ktx_cli image.ktx --info
    """)


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog='ktx_cli',
        description=(
            'Export KTX textures to PNG/JPEG from a local or SSH-remote path.\n'
            'The output image has a title bar with the original filename, so it\n'
            'looks like a screenshot taken from an image viewer.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )

    p.add_argument(
        'input', nargs='?', default=None,
        metavar='INPUT',
        help=(
            'Local .ktx file path.  '
            'May also be an SSH URL: "ssh://user:pass@host/path/file.ktx".'
        ),
    )

    ssh = p.add_argument_group('SSH options  (alternative to embedding credentials in the URL)')
    ssh.add_argument('--host',     metavar='HOST',
                     help='SSH server hostname or IP address.')
    ssh.add_argument('--port',     metavar='PORT',     type=int, default=22,
                     help='SSH port  (default: 22).')
    ssh.add_argument('--user',     metavar='USER',     default='root',
                     help='SSH username  (default: root).')
    ssh.add_argument('--password', metavar='PASS',     default='alpine',
                     help='SSH password  (default: alpine).')
    ssh.add_argument('--key',      metavar='KEY_FILE',
                     help='Path to an SSH private key file  (skips password auth).')
    ssh.add_argument('--remote',   metavar='REMOTE_PATH',
                     help='Remote file path on the SSH server.')

    out = p.add_argument_group('output options')
    out.add_argument(
        '-o', '--output', metavar='OUTPUT',
        help=(
            'Output image path  '
            '(default: same directory as INPUT with .png extension, '
            'or current directory for remote files).'
        ),
    )
    out.add_argument(
        '--format', choices=['png', 'jpg', 'jpeg'], default=None,
        help='Output format: png or jpg  (inferred from -o extension when omitted).',
    )
    out.add_argument(
        '--no-title', action='store_true',
        help='Do not add the filename title-bar overlay to the exported image.',
    )
    out.add_argument(
        '--info', action='store_true',
        help='Print image metadata to stdout and exit without writing an output file.',
    )
    return p


def main():
    import argparse

    # ── Dependency check ───────────────────────────────────────────────────
    missing = []
    if lzfse is None:
        missing.append('lzfse')
    if texture2ddecoder is None:
        missing.append('texture2ddecoder')
    if not HAS_PIL:
        missing.append('Pillow')
    if missing:
        print(f'Error: missing packages: {", ".join(missing)}', file=sys.stderr)
        print(f'Install with:  pip install {" ".join(missing)}', file=sys.stderr)
        sys.exit(1)

    parser = _build_parser()
    args   = parser.parse_args()

    # ── Resolve source ─────────────────────────────────────────────────────
    raw_data = None
    filename  = ''

    if args.input and args.input.lower().startswith('ssh://'):
        # ── SSH URL mode ─────────────────────────────────────────────────
        parsed = _parse_ssh_url(args.input)
        if parsed is None:
            parser.error('Could not parse SSH URL.  Expected: ssh://user:pass@host/path')
        print(f'Connecting to {parsed["host"]}:{parsed["port"]} …', flush=True)
        try:
            raw_data = fetch_remote(
                host=parsed['host'], port=parsed['port'],
                username=parsed['username'], password=parsed['password'],
                key_path='', remote_path=parsed['remote'],
            )
        except Exception as exc:
            print(f'SSH error: {exc}', file=sys.stderr)
            sys.exit(1)
        filename = os.path.basename(parsed['remote'])

    elif args.host and args.remote:
        # ── Explicit SSH flag mode ────────────────────────────────────────
        print(f'Connecting to {args.host}:{args.port} …', flush=True)
        try:
            raw_data = fetch_remote(
                host=args.host, port=args.port,
                username=args.user, password=args.password,
                key_path=args.key or '', remote_path=args.remote,
            )
        except Exception as exc:
            print(f'SSH error: {exc}', file=sys.stderr)
            sys.exit(1)
        filename = os.path.basename(args.remote)

    elif args.input:
        # ── Local file mode ───────────────────────────────────────────────
        if not os.path.isfile(args.input):
            parser.error(f'File not found: {args.input}')
        with open(args.input, 'rb') as f:
            raw_data = f.read()
        filename = os.path.basename(args.input)

    else:
        parser.print_help()
        sys.exit(0)

    # ── Decode ─────────────────────────────────────────────────────────────
    print(f'Decoding {filename} …', flush=True)
    try:
        img, info = _decode_ktx_bytes(raw_data)
    except Exception as exc:
        print(f'Decode error: {exc}', file=sys.stderr)
        sys.exit(1)

    # ── --info mode ────────────────────────────────────────────────────────
    if args.info:
        w = max(len(v) for v in [filename, info['type'] + ' KTX',
                                  info['format'], info['gl']])
        bar = '─' * (w + 20)
        print(bar)
        print(f'  File    {filename}')
        print(f'  Type    {info["type"]} KTX')
        print(f'  Size    {info["width"]} × {info["height"]} px')
        print(f'  Format  {info["format"]}  ({info["gl"]})')
        print(f'  Compressed  {info["file_size"]:>10,} bytes')
        print(f'  Raw ASTC    {info["raw_size"]:>10,} bytes')
        print(bar)
        sys.exit(0)

    # ── Title bar overlay ──────────────────────────────────────────────────
    if not args.no_title:
        img = add_title_bar(img, filename)

    # ── Determine output format ────────────────────────────────────────────
    fmt = args.format  # may be None
    if fmt is None:
        if args.output:
            ext = os.path.splitext(args.output)[1].lower()
            fmt = 'jpeg' if ext in ('.jpg', '.jpeg') else 'png'
        else:
            fmt = 'png'
    fmt = 'jpeg' if fmt in ('jpg', 'jpeg') else 'png'

    # ── Determine output path ──────────────────────────────────────────────
    stem = os.path.splitext(filename)[0]
    if args.output:
        out_path = os.path.expanduser(args.output)
    else:
        is_local = (args.input and not args.input.lower().startswith('ssh://')
                    and not args.host)
        out_dir  = os.path.dirname(os.path.abspath(args.input)) if is_local else os.getcwd()
        out_path = os.path.join(out_dir, stem + ('.jpg' if fmt == 'jpeg' else '.png'))

    # ── Convert for JPEG (drop alpha) ─────────────────────────────────────
    if fmt == 'jpeg':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'RGBA':
            bg.paste(img, mask=img.split()[3])
        else:
            bg.paste(img)
        img = bg

    # ── Save ───────────────────────────────────────────────────────────────
    try:
        img.save(out_path, format=fmt.upper())
    except Exception as exc:
        print(f'Save error: {exc}', file=sys.stderr)
        sys.exit(1)

    print(f'Saved  {img.width} × {img.height} px  →  {out_path}')


if __name__ == '__main__':
    main()
