"""
KTX Image Viewer for Windows
Supports Apple KTX (AAPL) and standard KTX 1.0 files with ASTC compression.
Dependencies: pip install lzfse texture2ddecoder Pillow
"""

import struct
import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    import lzfse
except ImportError:
    lzfse = None

try:
    import texture2ddecoder
except ImportError:
    texture2ddecoder = None

# ── Constants ────────────────────────────────────────────────────────────────

APPLE_KTX_MAGIC = b'AAPL\r\n\x1a\n'
STD_KTX1_MAGIC = b'\xabKTX 11\xbb\r\n\x1a\n'

ASTC_FORMATS = {
    0x93B0: (4, 4),   0x93B1: (5, 4),   0x93B2: (5, 5),
    0x93B3: (6, 5),   0x93B4: (6, 6),   0x93B5: (8, 5),
    0x93B6: (8, 6),   0x93B7: (8, 8),   0x93B8: (10, 5),
    0x93B9: (10, 6),  0x93BA: (10, 8),  0x93BB: (10, 10),
    0x93BC: (12, 10), 0x93BD: (12, 12),
    # SRGB variants
    0x93D0: (4, 4),   0x93D1: (5, 4),   0x93D2: (5, 5),
    0x93D3: (6, 5),   0x93D4: (6, 6),   0x93D5: (8, 5),
    0x93D6: (8, 6),   0x93D7: (8, 8),   0x93D8: (10, 5),
    0x93D9: (10, 6),  0x93DA: (10, 8),  0x93DB: (10, 10),
    0x93DC: (12, 10), 0x93DD: (12, 12),
}

KTX_EXTENSIONS = ('.ktx',)

# ── KTX Parsing ──────────────────────────────────────────────────────────────

def detect_ktx_type(data):
    if data[:8] == APPLE_KTX_MAGIC:
        return 'apple'
    if data[:12] == STD_KTX1_MAGIC:
        return 'standard'
    return None


def parse_apple_ktx(data):
    """Parse Apple-variant KTX file (AAPL magic)."""
    gl_internal = struct.unpack_from('<I', data, 0x20)[0]
    gl_base = struct.unpack_from('<I', data, 0x24)[0]
    width = struct.unpack_from('<I', data, 0x28)[0]
    height = struct.unpack_from('<I', data, 0x2C)[0]

    if gl_internal not in ASTC_FORMATS:
        raise ValueError(f"Unsupported GL internal format: 0x{gl_internal:04X}")

    bw, bh = ASTC_FORMATS[gl_internal]

    # Find LZFSE compressed data (bvx2/bvx1/bvxn/bvx- signatures)
    compressed_start = None
    for sig in (b'bvx2', b'bvx1', b'bvxn', b'bvx-'):
        pos = data.find(sig)
        if pos >= 0:
            compressed_start = pos
            break

    if compressed_start is not None:
        if lzfse is None:
            raise ImportError("lzfse package required: pip install lzfse")
        end_pos = data.rfind(b'END ')
        compressed = data[compressed_start:end_pos] if end_pos > compressed_start else data[compressed_start:]
        raw_astc = lzfse.decompress(compressed)
    else:
        # No LZFSE – try raw ASTC after header
        header_size = struct.unpack_from('<I', data, 8)[0]
        raw_astc = data[8 + header_size:]

    return width, height, bw, bh, raw_astc, gl_internal


def parse_standard_ktx(data):
    """Parse standard KTX 1.0 file."""
    endianness = struct.unpack_from('<I', data, 12)[0]
    if endianness == 0x04030201:
        fmt = '<'
    else:
        fmt = '>'

    gl_type = struct.unpack_from(f'{fmt}I', data, 16)[0]
    gl_type_size = struct.unpack_from(f'{fmt}I', data, 20)[0]
    gl_format = struct.unpack_from(f'{fmt}I', data, 24)[0]
    gl_internal = struct.unpack_from(f'{fmt}I', data, 28)[0]
    gl_base = struct.unpack_from(f'{fmt}I', data, 32)[0]
    width = struct.unpack_from(f'{fmt}I', data, 36)[0]
    height = struct.unpack_from(f'{fmt}I', data, 40)[0]
    depth = struct.unpack_from(f'{fmt}I', data, 44)[0]
    n_array = struct.unpack_from(f'{fmt}I', data, 48)[0]
    n_faces = struct.unpack_from(f'{fmt}I', data, 52)[0]
    n_mip = struct.unpack_from(f'{fmt}I', data, 56)[0]
    kv_bytes = struct.unpack_from(f'{fmt}I', data, 60)[0]

    # Skip key-value data
    data_offset = 64 + kv_bytes

    if gl_internal not in ASTC_FORMATS:
        raise ValueError(f"Unsupported GL internal format: 0x{gl_internal:04X}")

    bw, bh = ASTC_FORMATS[gl_internal]

    # First mip level
    image_size = struct.unpack_from(f'{fmt}I', data, data_offset)[0]
    raw_astc = data[data_offset + 4: data_offset + 4 + image_size]

    return width, height, bw, bh, raw_astc, gl_internal


def decode_ktx_file(filepath):
    """Load and decode a KTX file, returning a PIL Image and metadata."""
    with open(filepath, 'rb') as f:
        data = f.read()

    ktx_type = detect_ktx_type(data)
    if ktx_type == 'apple':
        width, height, bw, bh, raw_astc, gl_fmt = parse_apple_ktx(data)
    elif ktx_type == 'standard':
        width, height, bw, bh, raw_astc, gl_fmt = parse_standard_ktx(data)
    else:
        raise ValueError("Not a recognized KTX file")

    if texture2ddecoder is None:
        raise ImportError("texture2ddecoder package required: pip install texture2ddecoder")

    rgba = texture2ddecoder.decode_astc(raw_astc, width, height, bw, bh)
    img = Image.frombuffer('RGBA', (width, height), rgba, 'raw', 'BGRA')

    info = {
        'type': ktx_type,
        'width': width,
        'height': height,
        'format': f'ASTC {bw}x{bh}',
        'gl_internal': f'0x{gl_fmt:04X}',
        'compressed_size': len(data),
        'raw_size': len(raw_astc),
    }
    return img, info


# ── GUI ──────────────────────────────────────────────────────────────────────

class KTXViewer(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self, initial_file=None):
        super().__init__()
        self.title("KTX Viewer")
        self.geometry("900x700")
        self.configure(bg='#1e1e1e')
        self.minsize(400, 300)

        self.current_image = None
        self.photo_image = None
        self.zoom_level = 1.0
        self.file_list = []
        self.file_index = -1
        self.drag_data = {'x': 0, 'y': 0}

        self._build_ui()
        self._bind_keys()

        if initial_file:
            self.after(100, lambda: self.load_file(initial_file))

    def _build_ui(self):
        # Top toolbar
        toolbar = tk.Frame(self, bg='#2d2d2d', height=36)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        toolbar.pack_propagate(False)

        btn_style = dict(bg='#3c3c3c', fg='#cccccc', activebackground='#505050',
                         activeforeground='white', bd=0, padx=10, pady=4,
                         font=('Segoe UI', 9))

        tk.Button(toolbar, text="Open", command=self.open_file, **btn_style).pack(side=tk.LEFT, padx=(6, 2), pady=4)
        tk.Button(toolbar, text="< Prev", command=self.prev_file, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text="Next >", command=self.next_file, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)

        sep = tk.Frame(toolbar, bg='#555555', width=1)
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        tk.Button(toolbar, text="Zoom +", command=self.zoom_in, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text="Zoom -", command=self.zoom_out, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text="Fit", command=self.zoom_fit, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text="1:1", command=self.zoom_reset, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)

        sep2 = tk.Frame(toolbar, bg='#555555', width=1)
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        tk.Button(toolbar, text="Export PNG", command=self.export_png, **btn_style).pack(side=tk.LEFT, padx=2, pady=4)

        # Zoom label
        self.zoom_label = tk.Label(toolbar, text="100%", bg='#2d2d2d', fg='#888888',
                                   font=('Segoe UI', 9))
        self.zoom_label.pack(side=tk.RIGHT, padx=10)

        # Canvas for image display
        self.canvas = tk.Canvas(self, bg='#1e1e1e', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status_bar = tk.Label(self, text="Open a .ktx file or drag & drop one here",
                                   bg='#007acc', fg='white', anchor=tk.W,
                                   font=('Segoe UI', 9), padx=8, pady=3)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Canvas mouse bindings for pan
        self.canvas.bind('<ButtonPress-1>', self._on_pan_start)
        self.canvas.bind('<B1-Motion>', self._on_pan_move)
        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        self.canvas.bind('<Configure>', self._on_resize)

        # Drag-and-drop
        if HAS_DND:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind('<<Drop>>', self._on_drop)
            self.canvas.dnd_bind('<<DragEnter>>', self._on_drag_enter)
            self.canvas.dnd_bind('<<DragLeave>>', self._on_drag_leave)

    def _bind_keys(self):
        self.bind('<Control-o>', lambda e: self.open_file())
        self.bind('<Left>', lambda e: self.prev_file())
        self.bind('<Right>', lambda e: self.next_file())
        self.bind('<plus>', lambda e: self.zoom_in())
        self.bind('<equal>', lambda e: self.zoom_in())
        self.bind('<minus>', lambda e: self.zoom_out())
        self.bind('<Key-0>', lambda e: self.zoom_fit())
        self.bind('<Key-1>', lambda e: self.zoom_reset())
        self.bind('<Control-s>', lambda e: self.export_png())
        self.bind('<Escape>', lambda e: self.destroy())

    # ── File operations ──────────────────────────────────────────────────

    def open_file(self):
        filepath = filedialog.askopenfilename(
            title="Open KTX Image",
            filetypes=[("KTX Files", "*.ktx"), ("All Files", "*.*")]
        )
        if filepath:
            self.load_file(filepath)

    def load_file(self, filepath):
        filepath = os.path.abspath(filepath)
        self.status_bar.config(text=f"Loading: {os.path.basename(filepath)}...")
        self.update_idletasks()

        try:
            img, info = decode_ktx_file(filepath)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load KTX file:\n{e}")
            self.status_bar.config(text=f"Error: {e}")
            return

        self.current_image = img
        self.current_filepath = filepath

        # Build file list from same directory
        folder = os.path.dirname(filepath)
        self.file_list = sorted([
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(KTX_EXTENSIONS)
        ])
        self.file_index = self.file_list.index(filepath) if filepath in self.file_list else 0

        # Auto-fit
        self.zoom_fit()

        # Status
        name = os.path.basename(filepath)
        idx = f"[{self.file_index + 1}/{len(self.file_list)}]" if self.file_list else ""
        status = (f"{idx}  {name}  |  {info['width']}x{info['height']}  |  "
                  f"{info['format']}  |  {info['type'].title()} KTX  |  "
                  f"{info['compressed_size']:,} bytes")
        self.status_bar.config(text=status)
        self.title(f"KTX Viewer - {name}")

    def prev_file(self):
        if not self.file_list:
            return
        self.file_index = (self.file_index - 1) % len(self.file_list)
        self.load_file(self.file_list[self.file_index])

    def next_file(self):
        if not self.file_list:
            return
        self.file_index = (self.file_index + 1) % len(self.file_list)
        self.load_file(self.file_list[self.file_index])

    def export_png(self):
        if self.current_image is None:
            return
        default_name = os.path.splitext(os.path.basename(self.current_filepath))[0] + '.png'
        filepath = filedialog.asksaveasfilename(
            title="Export as PNG",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG Image", "*.png"), ("JPEG Image", "*.jpg"), ("All Files", "*.*")]
        )
        if filepath:
            self.current_image.save(filepath)
            self.status_bar.config(text=f"Exported: {filepath}")

    # ── Zoom / Display ───────────────────────────────────────────────────

    def _update_display(self):
        if self.current_image is None:
            return

        w = int(self.current_image.width * self.zoom_level)
        h = int(self.current_image.height * self.zoom_level)
        w = max(1, w)
        h = max(1, h)

        resample = Image.NEAREST if self.zoom_level > 2.0 else Image.LANCZOS
        resized = self.current_image.resize((w, h), resample)
        self.photo_image = ImageTk.PhotoImage(resized)

        self.canvas.delete('all')
        cx = self.canvas.winfo_width() // 2
        cy = self.canvas.winfo_height() // 2
        self.canvas.create_image(cx, cy, image=self.photo_image, anchor=tk.CENTER, tags='img')

        self.canvas.config(scrollregion=self.canvas.bbox('all'))
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")

    def zoom_in(self):
        self.zoom_level = min(self.zoom_level * 1.25, 16.0)
        self._update_display()

    def zoom_out(self):
        self.zoom_level = max(self.zoom_level / 1.25, 0.05)
        self._update_display()

    def zoom_reset(self):
        self.zoom_level = 1.0
        self._update_display()

    def zoom_fit(self):
        if self.current_image is None:
            return
        cw = self.canvas.winfo_width() or 900
        ch = self.canvas.winfo_height() or 650
        iw = self.current_image.width
        ih = self.current_image.height
        if iw == 0 or ih == 0:
            return
        self.zoom_level = min(cw / iw, ch / ih, 1.0)
        self._update_display()

    def _on_resize(self, event):
        if self.current_image is not None:
            self._update_display()

    def _on_pan_start(self, event):
        self.drag_data['x'] = event.x
        self.drag_data['y'] = event.y

    def _on_pan_move(self, event):
        dx = event.x - self.drag_data['x']
        dy = event.y - self.drag_data['y']
        self.canvas.move('img', dx, dy)
        self.drag_data['x'] = event.x
        self.drag_data['y'] = event.y

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def _on_drop(self, event):
        # tkinterdnd2 returns a space-separated list; braces wrap paths with spaces
        raw = event.data.strip()
        # Parse Tk list format: items may be wrapped in {}
        files = []
        while raw:
            if raw.startswith('{'):
                end = raw.index('}')
                files.append(raw[1:end])
                raw = raw[end + 1:].strip()
            else:
                parts = raw.split(' ', 1)
                files.append(parts[0])
                raw = parts[1].strip() if len(parts) > 1 else ''
        ktx_files = [f for f in files if f.lower().endswith('.ktx')]
        if ktx_files:
            self.canvas.config(bg='#1e1e1e')
            self.load_file(ktx_files[0])
        else:
            messagebox.showwarning('Unsupported', 'Only .ktx files are supported.')
            self.canvas.config(bg='#1e1e1e')

    def _on_drag_enter(self, event):
        self.canvas.config(bg='#2a3a4a')

    def _on_drag_leave(self, event):
        self.canvas.config(bg='#1e1e1e')


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    # Check dependencies
    missing = []
    if lzfse is None:
        missing.append('lzfse')
    if texture2ddecoder is None:
        missing.append('texture2ddecoder')
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)

    initial = None
    if len(sys.argv) > 1:
        initial = sys.argv[1]
    else:
        # Auto-load first KTX in current directory
        cwd = os.getcwd()
        ktx_files = sorted([f for f in os.listdir(cwd) if f.lower().endswith(KTX_EXTENSIONS)])
        if ktx_files:
            initial = os.path.join(cwd, ktx_files[0])

    app = KTXViewer(initial_file=initial)
    app.mainloop()


if __name__ == '__main__':
    main()
