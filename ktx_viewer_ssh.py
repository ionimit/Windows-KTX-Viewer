"""
KTX Image Viewer with SSH/SCP Remote Browser
Based on ktx_viewer.py – adds a left panel to browse and load KTX files
directly from an SSH server (iOS, macOS, Linux).

Dependencies:
    pip install lzfse texture2ddecoder Pillow tkinterdnd2 paramiko
"""

import io
import json
import os
import stat
import struct
import sys
import threading
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

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    paramiko = None  # type: ignore

# ── Configuration ──────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.ktxviewer_ssh.json')

DEFAULT_CFG: dict = {
    'host':       '',
    'port':       22,
    'username':   'root',
    'password':   'alpine',
    'key_path':   '',
    'remote_dir': '/var/mobile',
}


def _load_cfg() -> dict:
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return {**DEFAULT_CFG, **json.load(f)}
    except Exception:
        return dict(DEFAULT_CFG)


def _save_cfg(c: dict) -> None:
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, indent=2)
    except Exception:
        pass


# ── KTX Parsing ─────────────────────────────────────────────────────────────

APPLE_KTX_MAGIC = b'AAPL\r\n\x1a\n'
STD_KTX1_MAGIC  = b'\xabKTX 11\xbb\r\n\x1a\n'

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
            compressed = data[compressed_start:end_pos] if end_pos > compressed_start else data[compressed_start:]
            raw_astc = lzfse.decompress(compressed)
        else:
            header_size = struct.unpack_from('<I', data, 8)[0]
            raw_astc = data[8 + header_size:]
        ktype = 'apple'

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
        ktype = 'standard'

    else:
        raise ValueError('Not a recognized KTX file (expected AAPL or KTX1 magic).')

    if texture2ddecoder is None:
        raise ImportError('texture2ddecoder required: pip install texture2ddecoder')

    rgba = texture2ddecoder.decode_astc(raw_astc, width, height, bw, bh)
    img  = Image.frombuffer('RGBA', (width, height), rgba, 'raw', 'BGRA')

    info = {
        'type':             ktype,
        'width':            width,
        'height':           height,
        'format':           f'ASTC {bw}x{bh}',
        'gl_internal':      f'0x{gl_internal:04X}',
        'compressed_size':  len(data),
        'raw_size':         len(raw_astc),
    }
    return img, info


def decode_ktx_file(filepath: str):
    """Load a local KTX file and decode it → (PIL.Image, info_dict)."""
    with open(filepath, 'rb') as f:
        return _decode_ktx_bytes(f.read())


# ── SSH Browser Panel ──────────────────────────────────────────────────────────

def _section_label(parent, text: str, bg: str, fg: str) -> tk.Frame:
    """A small bolded section header with a separator line."""
    frame = tk.Frame(parent, bg=bg)
    tk.Label(frame, text=text, bg=bg, fg=fg,
             font=('Segoe UI', 8, 'bold')).pack(side=tk.LEFT, padx=4)
    tk.Frame(frame, bg='#444444', height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))
    return frame


class SSHBrowserPanel(tk.Frame):
    BG      = '#252525'
    FG      = '#d4d4d4'
    E_BG    = '#3c3c3c'
    BTN_BG  = '#3c3c3c'
    BTN_ACT = '#505050'
    SEL_BG  = '#094771'
    LIST_BG = '#1e1e1e'
    DIR_FG  = '#9cdcfe'   # light blue – directories
    FILE_FG = '#ce9178'   # peach    – .ktx files

    def __init__(self, master, on_load, **kwargs):
        kwargs.setdefault('bg', self.BG)
        super().__init__(master, **kwargs)
        self._on_load    = on_load
        self._ssh        = None
        self._sftp       = None
        self._cur_dir    = '/'
        self._entries    = []   # list of (is_dir: bool, name: str)
        self._lock       = threading.Lock()
        self._cfg        = _load_cfg()
        self._build_ui()
        self._populate_fields()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        s  = dict(bg=self.BG, fg=self.FG, font=('Segoe UI', 9))
        e  = dict(bg=self.E_BG, fg=self.FG, insertbackground=self.FG,
                  font=('Segoe UI', 9), bd=0, relief=tk.FLAT,
                  highlightthickness=1, highlightbackground='#555555',
                  highlightcolor='#007acc')
        b  = dict(bg=self.BTN_BG, fg=self.FG, activebackground=self.BTN_ACT,
                  activeforeground='white', bd=0, padx=7, pady=3,
                  font=('Segoe UI', 9), cursor='hand2')

        # ── SSH Connection ────────────────────────────────────
        _section_label(self, 'SSH Connection', self.BG, self.FG).pack(
            fill=tk.X, padx=6, pady=(10, 3))

        # Host + port row
        rf = tk.Frame(self, bg=self.BG)
        rf.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(rf, text='Host:', width=6, anchor='w', **s).pack(side=tk.LEFT)
        self._host_var = tk.StringVar()
        tk.Entry(rf, textvariable=self._host_var, **e).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._port_var = tk.StringVar()
        tk.Entry(rf, textvariable=self._port_var, **e, width=5).pack(
            side=tk.LEFT, padx=(3, 0))

        # Username row
        uf = tk.Frame(self, bg=self.BG)
        uf.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(uf, text='User:', width=6, anchor='w', **s).pack(side=tk.LEFT)
        self._user_var = tk.StringVar()
        tk.Entry(uf, textvariable=self._user_var, **e).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        # Password row
        pf = tk.Frame(self, bg=self.BG)
        pf.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(pf, text='Pass:', width=6, anchor='w', **s).pack(side=tk.LEFT)
        self._pass_var = tk.StringVar()
        tk.Entry(pf, textvariable=self._pass_var, show='●', **e).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        # Private key row
        kf = tk.Frame(self, bg=self.BG)
        kf.pack(fill=tk.X, padx=6, pady=2)
        tk.Label(kf, text='Key:', width=6, anchor='w', **s).pack(side=tk.LEFT)
        self._key_var = tk.StringVar()
        tk.Entry(kf, textvariable=self._key_var, **e).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        tk.Button(kf, text='…', command=self._browse_key, **{**b, 'padx': 4}).pack(
            side=tk.LEFT)

        # Connect button + status
        cf = tk.Frame(self, bg=self.BG)
        cf.pack(fill=tk.X, padx=6, pady=(6, 4))
        self._connect_btn = tk.Button(
            cf, text='Connect', command=self._toggle_connect, **b)
        self._connect_btn.pack(side=tk.LEFT)
        self._status_dot = tk.Label(
            cf, text='●', fg='#888888', bg=self.BG, font=('Segoe UI', 13))
        self._status_dot.pack(side=tk.LEFT, padx=(8, 2))
        self._status_lbl = tk.Label(cf, text='Disconnected', **s)
        self._status_lbl.pack(side=tk.LEFT)

        # ── Remote Files ──────────────────────────────────────
        tk.Frame(self, bg='#3a3a3a', height=1).pack(fill=tk.X, padx=6, pady=(4, 0))
        _section_label(self, 'Remote Files', self.BG, self.FG).pack(
            fill=tk.X, padx=6, pady=(6, 3))

        # Directory path + Go + Up + Refresh
        df = tk.Frame(self, bg=self.BG)
        df.pack(fill=tk.X, padx=6, pady=2)
        self._dir_var = tk.StringVar()
        self._dir_entry = tk.Entry(df, textvariable=self._dir_var, **e)
        self._dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._dir_entry.bind('<Return>', lambda _e: self._go_dir())
        _b5 = {**b, 'padx': 5}
        tk.Button(df, text='Go',  command=self._go_dir,  **_b5).pack(side=tk.LEFT, padx=(3, 1))
        tk.Button(df, text='↑',   command=self._go_up,   **_b5).pack(side=tk.LEFT, padx=1)
        tk.Button(df, text='⟳',   command=self._refresh, **_b5).pack(side=tk.LEFT, padx=1)

        # Listbox with scrollbar
        lf = tk.Frame(self, bg=self.BG)
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 2))
        sb = tk.Scrollbar(lf, orient=tk.VERTICAL, bg=self.BG,
                          troughcolor=self.BG, width=10)
        self._listbox = tk.Listbox(
            lf, yscrollcommand=sb.set,
            bg=self.LIST_BG, fg=self.FG,
            selectbackground=self.SEL_BG, selectforeground='white',
            font=('Consolas', 9), bd=0, relief=tk.FLAT,
            highlightthickness=1, highlightbackground='#555555',
            activestyle='none',
        )
        sb.config(command=self._listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._listbox.bind('<Double-Button-1>', self._on_dbl_click)
        self._listbox.bind('<Return>',          self._on_dbl_click)

        # Status line for file browser
        self._browser_status = tk.Label(
            self, text='Not connected', anchor='w',
            bg=self.BG, fg='#888888', font=('Segoe UI', 8), padx=6)
        self._browser_status.pack(fill=tk.X)

        # Load button
        tk.Button(
            self, text='Load Selected File', command=self._load_selected,
            bg='#007acc', fg='white', activebackground='#005fa3',
            activeforeground='white', bd=0, pady=6,
            font=('Segoe UI', 9, 'bold'), cursor='hand2',
        ).pack(fill=tk.X, padx=6, pady=(4, 8))

    def _populate_fields(self):
        c = self._cfg
        self._host_var.set(c.get('host', ''))
        self._port_var.set(str(c.get('port', 22)))
        self._user_var.set(c.get('username', 'root'))
        self._pass_var.set(c.get('password', 'alpine'))
        self._key_var.set(c.get('key_path', ''))
        self._dir_var.set(c.get('remote_dir', '/var/mobile'))

    def _read_fields(self) -> dict:
        try:
            port = int(self._port_var.get())
        except ValueError:
            port = 22
        return {
            'host':       self._host_var.get().strip(),
            'port':       port,
            'username':   self._user_var.get().strip(),
            'password':   self._pass_var.get(),
            'key_path':   self._key_var.get().strip(),
            'remote_dir': self._dir_var.get().strip() or '/',
        }

    def _browse_key(self):
        path = filedialog.askopenfilename(
            title='Select SSH Private Key',
            filetypes=[
                ('Key files', '*.pem *.key *.rsa *.ed25519 *.ppk *'),
                ('All files', '*.*'),
            ],
        )
        if path:
            self._key_var.set(path)

    # ── Connection ──────────────────────────────────────────────────────────

    def _toggle_connect(self):
        if self._sftp is not None:
            self._disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        if not HAS_PARAMIKO:
            messagebox.showerror(
                'Missing dependency',
                'paramiko is not installed.\n\nRun:  pip install paramiko',
            )
            return
        cfg = self._read_fields()
        if not cfg['host']:
            self._set_conn_status('No host specified', error=True)
            return
        self._set_conn_status('Connecting…')
        self._connect_btn.config(state='disabled')

        def _worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                kw: dict = dict(
                    hostname=cfg['host'],
                    port=cfg['port'],
                    username=cfg['username'],
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )
                key_path = cfg['key_path']
                if key_path and os.path.isfile(key_path):
                    kw['key_filename'] = key_path
                else:
                    kw['password'] = cfg['password']
                client.connect(**kw)
                sftp = client.open_sftp()
                with self._lock:
                    self._ssh  = client
                    self._sftp = sftp
                _save_cfg(cfg)
                self._cfg = cfg
                start_dir = cfg['remote_dir'] or '/'
                self.after(0, lambda sd=start_dir: self._on_connected(sd))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_connect_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _disconnect(self):
        with self._lock:
            sftp = self._sftp
            ssh  = self._ssh
            self._sftp = None
            self._ssh  = None
        try:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()
        except Exception:
            pass
        self._listbox.delete(0, tk.END)
        self._entries = []
        self._set_conn_status('Disconnected')
        self._browser_status.config(text='Not connected')
        self._connect_btn.config(text='Connect', state='normal')

    def _on_connected(self, start_dir: str):
        host = self._cfg['host']
        self._set_conn_status(f'Connected  {host}', ok=True)
        self._connect_btn.config(text='Disconnect', state='normal')
        self._navigate(start_dir)

    def _on_connect_error(self, msg: str):
        self._set_conn_status(f'Error: {msg}', error=True)
        self._connect_btn.config(text='Connect', state='normal')
        with self._lock:
            self._sftp = None
            self._ssh  = None

    def _set_conn_status(self, msg: str, *, error=False, ok=False):
        color = '#e74c3c' if error else ('#27ae60' if ok else '#f0a500')
        self._status_dot.config(fg=color)
        self._status_lbl.config(text=msg)

    # ── Navigation ──────────────────────────────────────────────────────────

    def _go_dir(self):
        path = self._dir_var.get().strip() or '/'
        self._navigate(path)

    def _go_up(self):
        parent = self._cur_dir.rstrip('/')
        parent = parent.rsplit('/', 1)[0] if '/' in parent else '/'
        if not parent:
            parent = '/'
        self._navigate(parent)

    def _refresh(self):
        self._navigate(self._cur_dir)

    def _navigate(self, path: str):
        with self._lock:
            sftp = self._sftp
        if sftp is None:
            self._browser_status.config(text='Not connected')
            return
        self._browser_status.config(text=f'Loading {path}…')
        self._listbox.delete(0, tk.END)
        self._entries = []

        def _worker():
            try:
                with self._lock:
                    sftp = self._sftp
                if sftp is None:
                    return
                attrs = sftp.listdir_attr(path)
                dirs  = sorted(
                    [a.filename for a in attrs
                     if a.st_mode is not None and stat.S_ISDIR(a.st_mode)],
                    key=str.lower,
                )
                files = sorted(
                    [a.filename for a in attrs
                     if a.st_mode is not None
                     and not stat.S_ISDIR(a.st_mode)
                     and a.filename.lower().endswith('.ktx')],
                    key=str.lower,
                )
                self.after(0, lambda p=path, d=dirs, f=files: self._populate_list(p, d, f))
            except Exception as exc:
                self.after(0, lambda e=exc: self._browser_status.config(
                    text=f'Error: {e}'))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_list(self, path: str, dirs: list, files: list):
        self._cur_dir = path
        self._dir_var.set(path)
        # Save last navigated dir to config
        self._cfg['remote_dir'] = path
        _save_cfg(self._cfg)

        self._entries = [(True, d) for d in dirs] + [(False, f) for f in files]
        self._listbox.delete(0, tk.END)
        for is_dir, name in self._entries:
            label = f'▸ {name}/' if is_dir else f'  {name}'
            self._listbox.insert(tk.END, label)
        # Colour entries
        for i, (is_dir, _) in enumerate(self._entries):
            self._listbox.itemconfig(i, fg=self.DIR_FG if is_dir else self.FILE_FG)

        total = f'{len(dirs)} dirs · {len(files)} .ktx files'
        self._browser_status.config(text=f'{path}  [{total}]')

    def _on_dbl_click(self, event):
        sel = self._listbox.curselection()
        if not sel:
            return
        is_dir, name = self._entries[sel[0]]
        if is_dir:
            new_path = self._cur_dir.rstrip('/') + '/' + name
            self._navigate(new_path)
        else:
            self._load_file(name)

    def _load_selected(self):
        sel = self._listbox.curselection()
        if not sel:
            messagebox.showinfo('No selection', 'Select a .ktx file first.')
            return
        is_dir, name = self._entries[sel[0]]
        if is_dir:
            new_path = self._cur_dir.rstrip('/') + '/' + name
            self._navigate(new_path)
        else:
            self._load_file(name)

    def _load_file(self, filename: str):
        remote_path = self._cur_dir.rstrip('/') + '/' + filename
        with self._lock:
            sftp = self._sftp
        if sftp is None:
            self._browser_status.config(text='Not connected')
            return
        self._browser_status.config(text=f'Downloading {filename}…')

        def _worker():
            try:
                with self._lock:
                    sftp = self._sftp
                    ssh  = self._ssh
                if sftp is None or ssh is None:
                    return
                # Try SFTP first; fall back to `cat` via exec_command on
                # permission errors (common on jailbroken iOS where the SFTP
                # subsystem runs as a restricted user but SSH exec runs as root).
                data = None
                try:
                    buf = io.BytesIO()
                    sftp.getfo(remote_path, buf)
                    data = buf.getvalue()
                except IOError:
                    pass  # fall through to cat

                if data is None:
                    # Shell-safe quoting: wrap path in single quotes, escape any
                    # single quotes inside the path itself.
                    safe_path = remote_path.replace("'", "'\\''")
                    _, stdout, stderr = ssh.exec_command(f"cat '{safe_path}'")
                    data = stdout.read()
                    err  = stderr.read().decode(errors='replace').strip()
                    if not data and err:
                        raise RuntimeError(f'cat failed: {err}')

                self.after(0, lambda d=data, fn=filename, rp=remote_path: self._on_load(d, fn, rp))
            except Exception as exc:
                self.after(0, lambda e=exc: self._browser_status.config(
                    text=f'Download error: {e}'))

        threading.Thread(target=_worker, daemon=True).start()


# ── Main Window ───────────────────────────────────────────────────────────────

class KTXViewerSSH(TkinterDnD.Tk if HAS_DND else tk.Tk):  # type: ignore[misc]
    def __init__(self, initial_file=None):
        super().__init__()
        self.title('KTX Viewer SSH')
        self.geometry('1250x740')
        self.configure(bg='#1e1e1e')
        self.minsize(700, 420)

        self.current_image    = None
        self.photo_image      = None
        self.zoom_level       = 1.0
        self.file_list: list  = []
        self.file_index       = -1
        self.drag_data        = {'x': 0, 'y': 0}
        self.current_filepath = None

        self._build_ui()
        self._bind_keys()

        if initial_file:
            self.after(100, lambda: self.load_file(initial_file))

    def _build_ui(self):
        # ── Toolbar ──────────────────────────────────────────
        toolbar = tk.Frame(self, bg='#2d2d2d', height=36)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        toolbar.pack_propagate(False)

        bs = dict(bg='#3c3c3c', fg='#cccccc', activebackground='#505050',
                  activeforeground='white', bd=0, padx=10, pady=4,
                  font=('Segoe UI', 9))

        tk.Button(toolbar, text='Open Local', command=self.open_file,   **bs).pack(side=tk.LEFT, padx=(6, 2), pady=4)
        tk.Button(toolbar, text='< Prev',     command=self.prev_file,   **bs).pack(side=tk.LEFT, padx=2,     pady=4)
        tk.Button(toolbar, text='Next >',     command=self.next_file,   **bs).pack(side=tk.LEFT, padx=2,     pady=4)

        tk.Frame(toolbar, bg='#555555', width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        tk.Button(toolbar, text='Zoom +',    command=self.zoom_in,    **bs).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text='Zoom -',    command=self.zoom_out,   **bs).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text='Fit',       command=self.zoom_fit,   **bs).pack(side=tk.LEFT, padx=2, pady=4)
        tk.Button(toolbar, text='1:1',       command=self.zoom_reset, **bs).pack(side=tk.LEFT, padx=2, pady=4)

        tk.Frame(toolbar, bg='#555555', width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        tk.Button(toolbar, text='Export PNG', command=self.export_png, **bs).pack(side=tk.LEFT, padx=2, pady=4)

        self.zoom_label = tk.Label(toolbar, text='100%', bg='#2d2d2d',
                                   fg='#888888', font=('Segoe UI', 9))
        self.zoom_label.pack(side=tk.RIGHT, padx=10)

        # ── Main area ─────────────────────────────────────────
        main = tk.Frame(self, bg='#1e1e1e')
        main.pack(fill=tk.BOTH, expand=True)

        # Left SSH panel (fixed 290px wide)
        self.ssh_panel = SSHBrowserPanel(main, on_load=self._on_remote_load,
                                         width=290)
        self.ssh_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.ssh_panel.pack_propagate(False)

        # Thin vertical divider
        tk.Frame(main, bg='#3a3a3a', width=1).pack(side=tk.LEFT, fill=tk.Y)

        # Right canvas
        self.canvas = tk.Canvas(main, bg='#1e1e1e', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Status bar ────────────────────────────────────────
        self.status_bar = tk.Label(
            self,
            text='Open a local .ktx file, or connect via SSH to browse remote files',
            bg='#007acc', fg='white', anchor=tk.W,
            font=('Segoe UI', 9), padx=8, pady=3,
        )
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Canvas bindings
        self.canvas.bind('<ButtonPress-1>', self._on_pan_start)
        self.canvas.bind('<B1-Motion>',     self._on_pan_move)
        self.canvas.bind('<MouseWheel>',    self._on_mousewheel)
        self.canvas.bind('<Configure>',     self._on_resize)

        # Drag-and-drop (local files)
        if HAS_DND:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind('<<Drop>>',      self._on_drop)
            self.canvas.dnd_bind('<<DragEnter>>', self._on_drag_enter)
            self.canvas.dnd_bind('<<DragLeave>>', self._on_drag_leave)

    def _bind_keys(self):
        self.bind('<Control-o>', lambda e: self.open_file())
        self.bind('<Left>',      lambda e: self.prev_file())
        self.bind('<Right>',     lambda e: self.next_file())
        self.bind('<plus>',      lambda e: self.zoom_in())
        self.bind('<equal>',     lambda e: self.zoom_in())
        self.bind('<minus>',     lambda e: self.zoom_out())
        self.bind('<Key-0>',     lambda e: self.zoom_fit())
        self.bind('<Key-1>',     lambda e: self.zoom_reset())
        self.bind('<Control-s>', lambda e: self.export_png())
        self.bind('<Escape>',    lambda e: self.destroy())

    # ── File loading ────────────────────────────────────────────────────────

    def open_file(self):
        filepath = filedialog.askopenfilename(
            title='Open KTX Image',
            filetypes=[('KTX Files', '*.ktx'), ('All Files', '*.*')],
        )
        if filepath:
            self.load_file(filepath)

    def load_file(self, filepath: str):
        filepath = os.path.abspath(filepath)
        self.status_bar.config(text=f'Loading: {os.path.basename(filepath)}…')
        self.update_idletasks()
        try:
            img, info = decode_ktx_file(filepath)
        except Exception as exc:
            messagebox.showerror('Error', f'Failed to load KTX file:\n{exc}')
            self.status_bar.config(text=f'Error: {exc}')
            return

        # Build file list for Prev/Next
        folder = os.path.dirname(filepath)
        self.file_list = sorted([
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(KTX_EXTENSIONS)
        ])
        self.file_index = self.file_list.index(filepath) if filepath in self.file_list else 0
        self.current_filepath = filepath
        self._show_image(img, info, os.path.basename(filepath))

    def _on_remote_load(self, data: bytes, filename: str, remote_path: str):
        """Called by SSHBrowserPanel after a remote .ktx file was downloaded."""
        self.status_bar.config(text=f'Decoding {filename}…')
        self.update_idletasks()
        try:
            img, info = _decode_ktx_bytes(data)
        except Exception as exc:
            messagebox.showerror('Error', f'Failed to decode {filename}:\n{exc}')
            self.status_bar.config(text=f'Error: {exc}')
            return
        self.file_list  = []
        self.file_index = -1
        self.current_filepath = remote_path
        self._show_image(img, info, filename, remote=True)

    def _show_image(self, img, info: dict, name: str, remote=False):
        self.current_image = img
        self.zoom_fit()
        prefix = '[SSH] ' if remote else ''
        idx_part = ''
        if self.file_list and not remote:
            idx_part = f'[{self.file_index + 1}/{len(self.file_list)}]  '
        self.status_bar.config(text=(
            f'{prefix}{idx_part}{name}  |  '
            f'{info["width"]}×{info["height"]}  |  '
            f'{info["format"]}  |  '
            f'{info["type"].title()} KTX  |  '
            f'{info["compressed_size"]:,} bytes'
        ))
        self.title(f'KTX Viewer SSH – {name}')

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
        default = os.path.splitext(
            os.path.basename(self.current_filepath or 'image')
        )[0] + '.png'
        filepath = filedialog.asksaveasfilename(
            title='Export as PNG',
            defaultextension='.png',
            initialfile=default,
            filetypes=[('PNG Image', '*.png'), ('JPEG Image', '*.jpg'),
                       ('All Files', '*.*')],
        )
        if filepath:
            self.current_image.save(filepath)
            self.status_bar.config(text=f'Exported: {filepath}')

    # ── Zoom / Display ──────────────────────────────────────────────────────

    def _update_display(self):
        if self.current_image is None:
            return
        w = max(1, int(self.current_image.width  * self.zoom_level))
        h = max(1, int(self.current_image.height * self.zoom_level))
        resample = Image.NEAREST if self.zoom_level > 2.0 else Image.LANCZOS
        resized  = self.current_image.resize((w, h), resample)
        self.photo_image = ImageTk.PhotoImage(resized)
        self.canvas.delete('all')
        cx = self.canvas.winfo_width()  // 2
        cy = self.canvas.winfo_height() // 2
        self.canvas.create_image(cx, cy, image=self.photo_image,
                                 anchor=tk.CENTER, tags='img')
        self.canvas.config(scrollregion=self.canvas.bbox('all'))
        self.zoom_label.config(text=f'{int(self.zoom_level * 100)}%')

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
        cw = self.canvas.winfo_width()  or 800
        ch = self.canvas.winfo_height() or 650
        self.zoom_level = min(
            cw / self.current_image.width,
            ch / self.current_image.height,
            1.0,
        )
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
        raw = event.data.strip()
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


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    missing = []
    if lzfse is None:
        missing.append('lzfse')
    if texture2ddecoder is None:
        missing.append('texture2ddecoder')
    if missing:
        print(f'Missing packages: {", ".join(missing)}')
        print(f'Install with: pip install {" ".join(missing)}')
        sys.exit(1)

    if not HAS_PARAMIKO:
        print('Note: paramiko not installed – SSH features will be unavailable.')
        print('Install with: pip install paramiko')

    initial = None
    if len(sys.argv) > 1:
        initial = sys.argv[1]
    else:
        cwd = os.getcwd()
        ktx = sorted([f for f in os.listdir(cwd) if f.lower().endswith(KTX_EXTENSIONS)])
        if ktx:
            initial = os.path.join(cwd, ktx[0])

    app = KTXViewerSSH(initial_file=initial)
    app.mainloop()


if __name__ == '__main__':
    main()
