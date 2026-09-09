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
| `Run.bat --test` | Run the range unit tests and the end-to-end transfer self-tests |
| `Run.bat --probe <ip>` | Diagnose why a device cannot be reached |
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

---

## When a send fails

Run the built-in probe, pointing it at the IP shown on the device card:

```bat
Run.bat --probe 192.168.18.146
```

It walks the stack in order — local listener, firewall rules, network profile,
TCP reachability, then the BarqDrop handshake — and names the layer that fails.

**The usual cause is the receiving PC, not the sender.** Devices appear in each
other's lists because discovery announcements are *outbound* UDP, which Windows
always permits; the *inbound* TCP connection that carries the file is a
different matter. So a device can be perfectly visible and still unreachable,
which shows up as a transfer that fails with a connection timeout before the
progress bar moves.

Two things fix it, and both are needed — on the **receiving** machine:

1. Right-click `Allow-Firewall.bat` → **Run as administrator**. It allows
   `BarqDrop.exe` in a packaged install and `.venv\Scripts\pythonw.exe` in a
   source checkout, plus the ports themselves.
2. Set the network to **Private**. Windows blocks device-to-device traffic on
   *Public* networks no matter what rules exist —
   *Settings ▸ Network & Internet ▸ Wi-Fi ▸ your network ▸ Network profile type*.
   `Allow-Firewall.bat` offers to switch it for you.

A timeout that says *refused* rather than *timed out* means the opposite:
the host answered but nothing is listening — BarqDrop is not running there, or
the two sides are configured with different ports.

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

### Measured ceiling

`python tools/bench.py` times each stage in isolation, and the built-in speed
test measures the engine end to end with no disk involved. On one ordinary
laptop, running **both** endpoints in a single process (so each side gets half
of one CPU):

| Path | Throughput |
| --- | --- |
| AES-GCM encrypt / decrypt | ~900 MB/s |
| Raw TCP, no framing | ~1.0 GB/s |
| Framing layer, plain | ~630 MB/s |
| Framing layer, encrypted | ~277 MB/s |
| **Full engine, encrypted** | **375-400 MB/s  (3.1 Gbit/s)** |
| **Full engine, plain** | **600-800 MB/s  (6.4 Gbit/s)** |

A gigabit link needs 125 MB/s, so the engine clears it roughly three times over
with encryption on. In practice **the network is always the limit, not
BarqDrop** — which is what the speed test is for.

Tuning lives in **Settings**: stream count, chunk size, segment size, socket
buffer size, and payload encryption.

---

## Getting the most speed out of a link

Press **Speed** on a device card. It sends generated data that is discarded on
arrival — nothing touches either disk — so the number you get is the link
itself, with the app's overhead already included.

If that number disappoints, the fix is physical. What actually limits a
transfer, in descending order of impact:

**1. Wi-Fi through a router costs you half.** Every byte crosses the air twice:
sender → access point → receiver. Two devices on the same AP therefore share
the airtime, and a 400 Mbps link yields around 100 Mbps of file transfer.

Press **Direct** on a device card to try removing that hop. One PC hosts a
private Wi-Fi link and the other is asked — over the connection they already
share — to join it, so neither of you types an SSID or a password.

Whether it actually helps is hardware-dependent, and BarqDrop does not pretend
otherwise: a laptop with a single radio has to share airtime between hosting
the link and staying online, and Windows often narrows the soft AP's channel.
So the flow **measures the link before switching, measures it again after, and
shows you both numbers** with a one-click way back. If it is slower, take the
way back.

Two guardrails, because moving a machine onto a different network is not a
small thing to do to someone: only a device you have already paired with can
ask, and the machine being asked always gets a prompt naming the network it
would join. **Restore network** in the header undoes it from either side.

**2. Gigabit Ethernet is the only reliable way to actually reach 1 Gbit.** Wire
both machines and expect ~110 MB/s — no radio, no relay, no contention.

**3. Channel width matters more than the standard.** A 2x2 client at 80 MHz
gets 867 Mbps; the same client at 40 MHz gets 400. If `netsh wlan show
interfaces` reports a transmit rate near 400 Mbps on 5 GHz, the router is
almost certainly set to 40 MHz — widen it to 80 MHz in the router's 5 GHz
settings.

**4. Turn off payload encryption** (Settings) only once the link is fast enough
for it to matter. It is worth roughly 2x at multi-gigabit speeds and nothing
at all below ~250 MB/s, and it costs you the integrity check.

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
    directflow.py         measure -> switch -> re-measure -> keep or revert
    gui.py                the desktop UI
    theme.py              dark theme
    config.py, util.py    settings, identity, helpers
  Allow-Firewall.bat      firewall rules + network profile (run as admin)
  installer/
    barqdrop.iss          Inno Setup script
    Install.bat           per-user install from the portable folder
  tools/
    selftest.py           end-to-end transfer, resume, folder, decline tests
    test_longtransfer.py  regression test for a long-idle control channel
    test_directlink.py    direct-link invite flow, incl. who may ask
    test_ranges.py        unit tests for the resume range arithmetic
    bench.py              per-stage throughput benchmark
    probe.py              connectivity diagnosis
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
