# Windows KTX Viewer

Windows-native tools for viewing and extracting **Apple KTX** texture files from iOS app bundles. Three versions: GUI viewer, SSH remote browser, and CLI extractor.

## Features

### 🎨 KTXViewer.exe – Drag-and-Drop GUI
- Full-featured graphical viewer for KTX textures
- Drag-and-drop file support
- Zoom (scroll) and pan (right-click drag)
- Image metadata display
- Export to PNG / JPEG
- Supports Apple KTX (LZFSE+ASTC) and standard KTX 1.0

### 🌐 KTXViewerSSH.exe – GUI + SSH File Browser
- Everything from KTXViewer, plus:
- SSH connection UI with credential persistence
- Remote directory browser (color-coded files)
- Fetch files directly from jailbroken iOS devices
- SFTP with fallback to `cat` command (for sandbox bypass on iOS)

### ⌨️ ktx_cli.exe – Command-Line Extractor
- Extract KTX textures from local or remote paths
- Export to PNG/JPEG with automatic alpha compositing
- Metadata export (`--info` flag)
- SSH authentication via flags or embedded URLs

---

## Installation

### Pre-built Executables
Download from the [Releases](https://github.com/ionimit/Windows-KTX-Viewer/releases) page:
- **KTXViewer.exe** (33 MB) – Windowed GUI
- **KTXViewerSSH.exe** (37 MB) – GUI + SSH browser
- **ktx_cli.exe** (35 MB) – CLI tool

No installation required — just run the exe.

### Python Requirements
If building from source:
```powershell
pip install lzfse texture2ddecoder Pillow tkinterdnd2 paramiko
```

---

## Usage

### KTXViewer.exe
```
KTXViewer.exe [file.ktx]
```
**Features:**
- Drag-and-drop files onto the window
- **Zoom:** Mouse wheel
- **Pan:** Right-click + drag
- **Export:** File → Export as PNG/JPEG
- Shows: dimensions, format (ASTC), raw size, file size

### KTXViewerSSH.exe
```
KTXViewerSSH.exe
```
**SSH Connection:**
1. Enter hostname, port (default 22), username (default `root`), password (default `alpine`)
2. Select private key (optional)
3. Click "Connect"
4. Browse remote directories (dirs in blue, `.ktx` files in peach)
5. Double-click a file to load in the viewer

**Credentials** are saved locally to `~/.ktxviewer_ssh.json` on successful connection.

### ktx_cli.exe

#### Basic usage – local file:
```powershell
ktx_cli.exe image.ktx
```
Exports as `image.png` in the same directory.

#### Custom output:
```powershell
ktx_cli.exe image.ktx -o C:\output\splash.png
```

#### SSH – with direct credentials:
```powershell
ktx_cli.exe --host 192.168.1.100 --user root --password alpine \
  --remote /var/containers/Bundle/Application/.../splash.ktx
```

#### SSH – shorthand URL:
```powershell
ktx_cli.exe "ssh://root:alpine@192.168.1.100/var/.../splash.ktx"
```

#### SSH – with private key:
```powershell
ktx_cli.exe --host 192.168.1.100 --key ~/.ssh/id_rsa \
  --remote /var/mobile/app.ktx
```

#### Export as JPEG (alpha → white background):
```powershell
ktx_cli.exe image.ktx -o image.jpg
```

#### Metadata only (no export):
```powershell
ktx_cli.exe image.ktx --info
```

#### No title bar overlay:
```powershell
ktx_cli.exe image.ktx --no-title
```

#### Full help:
```powershell
ktx_cli.exe --help
```

---

## Format Support

### Apple KTX (`.ktx`)
- **Magic:** `AAPL\r\n\x1a\n`
- **Compression:** LZFSE-compressed ASTC
- **ASTC block sizes:** 4×4, 5×4, 5×5, 6×5, 6×6, 8×5, 8×6, 8×8, 10×5, 10×6, 10×8, 10×10, 12×10, 12×12
- **Source:** iOS app bundles (`*.bundle/SplashBoard/Snapshots/`)

### Standard KTX 1.0
- **Magic:** `\xabKTX 11\xbb\r\n\x1a\n`
- Full KTX 1.0 spec support

---

## Building from Source

### Prerequisites
```powershell
pip install lzfse texture2ddecoder Pillow tkinterdnd2 paramiko cryptography pyinstaller
```

### Build Individual Tools
Each builder script cleans old artifacts and rebuilds its executable while preserving others in `dist/`:

```powershell
# Build all three
.\build_ktx_viewer.ps1
.\build_ktx_viewer_ssh.ps1
.\build_ktx_cli.ps1

# Or build one at a time (outputs to dist/)
.\build_ktx_cli.ps1
```

### Manual Build
```powershell
pyinstaller --onefile --console --name ktx_cli \
  --collect-all lzfse --collect-all texture2ddecoder \
  --collect-all paramiko --collect-all cryptography \
  ktx_cli.py
```

---

## File Structure

```
KTXViewer/
├── ktx_viewer.py           # GUI viewer (local files)
├── ktx_viewer_ssh.py       # GUI viewer + SSH browser
├── ktx_cli.py              # CLI extractor
├── build_ktx_viewer.ps1    # Builder script
├── build_ktx_viewer_ssh.ps1
├── build_ktx_cli.ps1
├── LICENSE                 # MIT License
└── dist/
    ├── KTXViewer.exe
    ├── KTXViewerSSH.exe
    └── ktx_cli.exe
```

---

## SSH Configuration

### Default Credentials (iOS Jailbroken Device)
- **Host:** Device IP (192.168.x.x)
- **Port:** 22
- **Username:** `root`
- **Password:** `alpine` (default jailbreak password)
- **Key:** Optional (fallback if private key is unavailable)

### Credential Persistence (GUI tools)
Credentials are saved to `~/.ktxviewer_ssh.json` after successful connection for future use.

### SFTP → SSH Fallback
On sandboxed iOS systems, SFTP may fail. The tools automatically fall back to `cat` over SSH, which runs as root and bypasses sandbox restrictions.

---

## Decode Pipeline

1. **Parse KTX header** → extract dimensions, format, compression
2. **Decompress LZFSE** → raw ASTC binary data
3. **Decode ASTC** → raw RGBA bytes
4. **Render** → PIL Image or export to PNG/JPEG

Supports both Apple's LZFSE compression and standard KTX 1.0 (no compression).

---

## License

MIT License — see [LICENSE](LICENSE) file.

---

## Troubleshooting

### SSH Connection Fails
- Verify device is on the same network and SSH is enabled
- Check username/password (default: `root` / `alpine`)
- Try connecting via `ssh user@host` manually to confirm

### SFTP Permission Denied
- The tool automatically falls back to `cat` command
- Ensure SSH user has read permissions on the file path

### Texture Won't Decode
- Verify it's a real Apple `.ktx` file (check magic bytes)
- Ensure ASTC block size is supported
- Check file isn't corrupted (try `--info` first)

### Build Fails
- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Clear old builds: `Remove-Item -Recurse build, dist, *.spec`
- Regenerate: `.\build_ktx_cli.ps1`

---

## Quick Start Example

### Extract from jailbroken iPhone:
```powershell
# List available exes
.\dist\ktx_cli.exe --help

# Fetch and export from device
.\dist\ktx_cli.exe "ssh://root:alpine@192.168.1.100/var/containers/.../splash.ktx" \
  -o splash.png
```

### View in GUI:
```powershell
# Windowed GUI
.\dist\KTXViewer.exe splash.png

# Or use the SSH browser
.\dist\KTXViewerSSH.exe
```

---

**Questions?** Open an issue on [GitHub](https://github.com/ionimit/Windows-KTX-Viewer/issues).
