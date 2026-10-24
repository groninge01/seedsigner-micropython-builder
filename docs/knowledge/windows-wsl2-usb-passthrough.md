# Accessing an ESP32 board from Windows + WSL2

A USB board connected to Windows is not automatically visible inside WSL2. Use
**usbipd-win** to attach it to WSL, then grant the Linux user serial-port access.
Docker builds do not need USB access; flashing and serial monitoring run on the WSL host.

Verified with a Waveshare ESP32-P4 WiFi6 Touch LCD 4.3 (chip revision 3.2). Its
USB serial bridge appeared as `1a86:55d3`, **USB Single Serial**, at `/dev/ttyACM0`.
Other boards may appear as `/dev/ttyUSB0`; do not assume a fixed port or BUSID.

## 1. Install usbipd-win on Windows

In **Windows PowerShell**, not a WSL shell:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Reopen PowerShell after installation so `usbipd` is available on `PATH`.
If `usbipd` is “not recognized”, check installation and reopen the terminal.

## 2. Share and attach the board

Keep a WSL2 distribution running. In **Administrator PowerShell**:

```powershell
usbipd list
usbipd bind --busid <BUSID>
```

Replace `<BUSID>` with the board's identifier from the list, for example `2-4`.
Binding shares the device and normally needs doing only once per device.

Attach it from PowerShell; this step does not require Administrator privileges:

```powershell
usbipd attach --wsl --busid <BUSID>
usbipd list
```

While attached, the device is available to WSL rather than Windows serial tools.
Close any Windows serial monitor before attaching.

## 3. Check visibility and permissions in WSL

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* /dev/serial/by-id/ 2>/dev/null
id -nG
```

A typical device is owned by `root:dialout` with mode `660`. If your user is not
in `dialout`:

```bash
sudo usermod -aG dialout "$USER"
newgrp dialout
```

`usermod` changes group membership for future sessions. `newgrp` starts a shell
with the new group; it **does not update already-running editors, agents, or
other terminal sessions**. Restart those processes or use `sg` for an individual
command. Confirm membership and access without opening or resetting the board:

```bash
getent group dialout
sg dialout -c 'test -r /dev/ttyACM0 && test -w /dev/ttyACM0 && echo "Serial access OK"'
```

Do not work around permissions with world-writable device nodes or run the entire
editor as root.

## 4. Identify the chip before flashing

From the repository root, install host-side tools into an isolated environment:

```bash
python3 -m venv .tmp/flash-tools
.tmp/flash-tools/bin/python -m pip install esptool
sg dialout -c '.tmp/flash-tools/bin/python -m esptool --chip esp32p4 --port /dev/ttyACM0 flash-id'
```

Use the correct chip and port for your board. `flash-id` reads identification
information without writing flash, but normally resets the board. Close other
serial clients first; only one tool should own the port at a time.

Check **chip revision and flash size**, not just the board name. The tested board
reported revision **3.2** and **32 MB** flash. Older ESP32-P4 firmware images may
support only revisions through **1.99**. Do not bypass that check with `--force`:
revision-v3 silicon needs compatible ESP-IDF and a separately targeted build.
See `README-dev.md` for the toolchain and board configuration.

## Disconnects and common failure modes

- **No Linux serial device:** check `usbipd list` in Windows and attach the correct
  BUSID. An installed `usbipd` is not enough; the device must be attached.
- **Device visible, permission denied:** check the groups of the process actually
  running the flasher. A different terminal's `newgrp` does not affect it; use `sg`.
- **Port busy:** close Windows/WSL serial monitors and any previous flashing tool.
- **Cable unplugged, device re-enumerated, or WSL restarted:** check the BUSID again
  and repeat `usbipd attach --wsl --busid <BUSID>` as needed. Binding normally
  persists; attachment does not persist across unplugging. Device paths can change.
- **Return control to Windows:** run `usbipd detach --busid <BUSID>` in PowerShell.

Official setup reference: [Connect USB devices to WSL](https://learn.microsoft.com/windows/wsl/connect-usb).
