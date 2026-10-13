# LightSend for Linux

An Ubuntu/Linux port of [LightSend for PC](../LightSend_PC-main), written in Python with Tkinter. It looks and
works like the phone's send screen and transfers photos **over Wi-Fi** in both directions between this
computer and any phone (or other PC) running LightSend. The flashlight and NFC methods need phone hardware,
so they are shown but disabled.

It speaks the same wire format and the same mDNS service (`_lightsend._tcp`) as the Windows app and the
Android app, so all three interoperate.

## Requirements

- Ubuntu 22.04 or newer (any Linux with Python 3.8+ works)
- `python3-tk` and `python3-pil.imagetk` (Pillow with Tk support)

```bash
sudo apt install python3-tk python3-pil.imagetk
```

There are no other dependencies: multicast DNS is implemented in `lightsend/mdns.py`.
Optional extras: `pip install tkinterdnd2` enables dropping photos onto the window, and
`pip install pillow-heif` adds HEIC/HEIF photos.

## Running

```bash
python3 -m lightsend
```

`./install.sh` adds a LightSend launcher to the applications menu.

## Using it

### Send photos to a phone

1. On the phone, open LightSend, keep **Wi-Fi** selected and tap **Receive mode**.
2. Click **Choose photos from Pictures** (or click either photo box). Click photos to select up to 50; the
   numbers show the sending order. **Change folder…** browses another folder and is remembered.
3. Optionally pick an **Image size to send** (Original sends the file unchanged).
4. Click **Send**. The app looks for receivers for up to 10 seconds:
   - if exactly one is found, the photos are sent to it straight away;
   - if several are found, choose one in the **Send to** dialog;
   - if none is found (for example the network blocks multicast), type the address shown on the phone's
     receive screen, such as `192.168.1.20:41483`. The last address is remembered.

   Click **Stop** to cancel a transfer.

### Receive photos from a phone

1. Click **Receive mode**. The window shows the name this computer is visible as and its address(es).
2. On the phone, keep **Wi-Fi** selected, choose photos and tap **Send**. Choose this computer if asked.
3. Photos are saved to `~/Pictures/LightSend` (the XDG Pictures folder; a ` (1)` suffix is added if a name
   already exists). **Open folder** opens it in the file manager.

Both devices must be on the same network. If a firewall such as `ufw` is active, it must allow UDP 5353
(mDNS) and incoming TCP connections from the local network.

Preferences are stored in `~/.config/lightsend/settings.json`. The window follows the desktop's light/dark
setting; set `LIGHTSEND_THEME=dark` or `light` to override it.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Project layout

| Path | Contents |
| --- | --- |
| `lightsend/protocol.py` | Wire format, shared with the Android `WifiProtocol.kt`. |
| `lightsend/transfer.py` | TCP sender and receiver. |
| `lightsend/mdns.py`, `lightsend/discovery.py` | mDNS/DNS-SD advertising and discovery of `_lightsend._tcp`. |
| `lightsend/network.py` | LAN adapter selection (up, multicast, default gateway), address parsing. |
| `lightsend/imaging.py`, `lightsend/util.py` | Thumbnails and resizing, file saving, size formatting, settings. |
| `lightsend/ui/` | Tk windows: send screen, photo picker, receive window, receiver chooser. |
| `tests/` | Protocol, saver, address parsing and discovery tests. |

## Startup reporting

When the app starts, it makes a background GET request to `http://www.lightsend.net/client.html` for
aggregate usage tracking. The request has a two-second timeout and does not delay startup or show an error
if the endpoint cannot be reached.

## Differences from the Windows app

- Windows-specific parts (title bar colouring, Explorer, Windows network settings) are replaced by the Linux
  equivalents (`xdg-open`, GNOME/NetworkManager settings).

## Wire format

Each photo is one TCP connection to the receiver's advertised port:

```
magic "LSW1" (u32, big-endian) | name (u16 length + modified UTF-8) | size (i32) | CRC-32 (u32) | data | ack (1 byte: 1 = OK, 2 = damaged)
```

Several photos are sent one after another, one connection each.
