# BarqDrop

**AirDrop-style file transfer for Windows.** Two machines on the same Wi-Fi,
Ethernet segment or a direct device-to-device link find each other by
themselves, verify a 6-digit code once, and then move files over a direct,
encrypted TCP connection at whatever speed the hardware allows. No internet, no
cloud, no account, no size limit.

*Barq* (برق) means lightning.

---

## Quick start

```bat
Run.bat
```

That is all. `Run.bat` creates `.venv`, installs the dependencies into it, and
starts the app. Run it on both machines; each appears in the other's
**Nearby devices** list within a couple of seconds.

To send: drag files or folders onto the window (or onto a device card to send
instantly), then press **Send** on the device you want. The receiving side sees
the file list and a pairing code; once it accepts, the transfer starts.

Other entry points:

| Command | What it does |
| --- | --- |
| `Run.bat` | Start BarqDrop |
| `Run.bat --console` | Start it with a visible console for log output |
| `Run.bat --check` | Prepare the environment and print the installed versions |
| `Run.bat --test` | Run the range unit tests and the end-to-end transfer self-test |
| `Build.bat` | Build the standalone Windows package and installer |
| `Build.bat --onefile` | Also emit a single portable `BarqDrop.exe` |

---

## Building a standalone Windows package

```bat
Build.bat
```

`Build.bat` verifies the engine with the self-test, generates the icon, and runs
PyInstaller. Output in `dist\`:

| File | Notes |
| --- | --- |
| `BarqDrop\BarqDrop.exe` | The application. No Python required on the target machine. |
| `BarqDrop-Setup-1.0.0.exe` | Windows installer — needs [Inno Setup 6](https://jrsoftware.org/isdl.php) on the build machine. Adds shortcuts and firewall rules, and registers an uninstaller. |
| `BarqDrop-1.0.0-portable.zip` | Unzip anywhere and run. Contains `Install.bat` for a per-user install with shortcuts, and `Allow-Firewall.bat`. |
| `BarqDrop-1.0.0-portable.exe` | Single-file build (`Build.bat --onefile`). |

If Inno Setup is not installed the build still succeeds — you get the portable
zip and `Install.bat` instead of the `.exe` installer.

---

## How it connects

BarqDrop needs the two devices to share an IP subnet. It never needs an
internet connection, and traffic never leaves the local link.

1. **Any shared network** — same Wi-Fi, same router, same Ethernet switch. This
   is the normal case and needs no setup at all.
2. **Direct Wi-Fi link** — the **Start direct Wi-Fi link** button in the header
   creates a router-free device-to-device link:
   * first via the Windows **Mobile Hotspot** API
     (`NetworkOperatorTetheringManager`), which on Wi-Fi Direct capable adapters
     is implemented as a Wi-Fi Direct soft AP;
   * otherwise via the legacy `netsh wlan start hostednetwork`.

   BarqDrop shows the network name and password to join from the other device.
   Whether either path works depends on the Wi-Fi driver — the app tells you
   plainly if the adapter refuses, rather than pretending. A phone hotspot with
   both machines joined works just as well and stays fully local.

Discovery is a small UDP announcement on **45877**, sent both to every
interface's broadcast address and to a multicast group, because some Wi-Fi
drivers drop one but not the other. Transfers use TCP **45878**.

If the two machines cannot see each other, it is almost always Windows
Firewall: run `Allow-Firewall.bat` as administrator (the installer does this for
you), and make sure the network is marked *Private*, not *Public*.

---

## How the speed is achieved

| Technique | Effect |
| --- | --- |
| **Parallel TCP streams** (4 by default, up to 16) | Several connections pull from one shared work queue, so a single slow stream cannot hold the transfer back and the link stays saturated. |
| **Large segments** (32 MB) and **large chunks** (4 MB) | Very few syscalls and headers per gigabyte; per-record overhead becomes noise. |
| **Preallocated reusable buffers** | `readinto` / `recv_into` into one buffer per stream — no per-chunk allocation, no garbage-collector pressure in the hot loop. |
| **Big socket buffers + `TCP_NODELAY`** | 4 MB send/receive windows keep long fat links busy; no Nagle delay on the small headers. |
| **Preallocated destination file, one handle per stream** | Each stream does positional writes into a file laid out once, with no locking and no fragmentation. |
| **Hardware-accelerated AES-GCM** | Encryption rides on AES-NI. It can also be switched off in Settings when the link is already trusted. |
| **No compression, no re-encoding** | Bytes go from disk to socket to disk unchanged. |

On loopback the engine sustains **~150-210 MB/s** with 4 streams; on a real
network the link is the limit — expect roughly 110 MB/s on gigabit Ethernet and
40-160 MB/s on Wi-Fi 5/6, which is where a transfer should be.

Tuning lives in **Settings**: stream count, chunk size, segment size, socket
buffer size, and payload encryption.

---

## Resume, retry, integrity

* Incoming data is written into a preallocated `<name>.barqpart` beside its
  destination, with a sidecar `.barqpart.json` recording exactly which byte
  ranges have landed.
* If the transfer is cancelled, the network drops, or the app is closed, the
  partial file stays. Send the same file to the same folder again and the
  receiver reports its missing ranges — only those are re-sent.
* Within a transfer, an individual stream that fails reconnects on its own
  (with backoff) and its unfinished segment goes back on the queue for any
  stream to pick up. Only fully received segments are ever marked done, so a
  half-written segment is always re-sent rather than silently accepted.
* Every payload chunk in encrypted mode is AES-GCM authenticated, so corruption
  or tampering fails the transfer instead of landing on disk. With payload
  encryption switched off there is no integrity check beyond TCP checksums --
  that is the trade you make for the last few percent of throughput.
* The file is renamed into place only once every byte is accounted for. An
  existing file of the same name is never overwritten — the new one becomes
  `name (2).ext`.

---

## Security model

* **X25519 key agreement** on connect, in a Noise-XX style pattern binding both
  devices' persistent identity keys plus fresh ephemeral keys.
* **A 6-digit pairing code** (a short authentication string derived from the
  full handshake transcript) is shown on both screens. Comparing it once rules
  out an active man-in-the-middle and pins the peer's identity.
* **Trust on first use** — after a successful pairing the peer's identity
  fingerprint is remembered and the code is not asked for again. "Forget all
  paired devices" is in Settings.
* **AES-GCM** protects the control channel always, and the file payload by
  default. Each parallel stream and each reconnection derives its own key, so a
  nonce is never reused.
* Incoming paths are sanitised (no traversal, no reserved Windows names) before
  anything is written, and every offer requires explicit acceptance unless you
  enable auto-accept for devices you already paired.

---

## Layout

```
barqdrop/
  Run.bat                 create/use .venv, install deps, launch
  Build.bat               standalone .exe + installer
  barqdrop.spec           PyInstaller configuration
  requirements.txt        runtime dependencies
  requirements-build.txt  build-only dependencies
  run_barqdrop.py         launcher entry point
  barqdrop/
    engine.py             transfer engine: server, sender, parallel streams
    protocol.py           framing, socket tuning, control + data channels
    crypto.py             X25519 handshake, pairing code, AES-GCM records
    discovery.py          UDP broadcast + multicast peer discovery
    resume.py             partial files and byte-range bookkeeping
    link.py               link speed reporting, Wi-Fi Direct / hotspot
    gui.py                the desktop UI
    theme.py              dark theme
    config.py, util.py    settings, identity, helpers
  installer/
    barqdrop.iss          Inno Setup script
    Install.bat           per-user install from the portable folder
    Allow-Firewall.bat    firewall rules (run as administrator)
  tools/
    selftest.py           end-to-end transfer, resume, folder, decline tests
    test_ranges.py        unit tests for the resume range arithmetic
    test_gui.py           offscreen UI smoke test
    make_icon.py          renders assets/barqdrop.ico
```

Settings, the device identity key and the paired-device list live in
`%APPDATA%\BarqDrop\config.json`. Received files default to
`%USERPROFILE%\Downloads\BarqDrop`.

---

## Requirements

* Windows 10 or 11, 64-bit
* Python 3.9+ **to run from source or to build** — not needed to run the built
  `.exe`
* `PySide6`, `cryptography`, `psutil`; `winsdk` is optional and only enables the
  Mobile Hotspot / Wi-Fi Direct path
