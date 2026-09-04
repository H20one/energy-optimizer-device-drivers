# Aurora Power-One — PV Inverter Driver Reference

**Driver ID:** `aurora_rs485`
**Device type:** `pv_inverter`
**Connection:** RS-485 serial (or TCP socket bridge)
**Manufacturer:** Power-One (ABB)
**Supported models:** Aurora PVI grid-tied series, Aurora UNO single-phase; ABB-branded Aurora models (same protocol after acquisition)

> For the data contract this driver must satisfy, see [pv_inverter.md](../contracts/pv_inverter.md).

---

## Configuration

| Field      | Type   | Required | Default        | Description                                                        |
| ---------- | ------ | :------: | -------------- | ------------------------------------------------------------------ |
| `address`  | number |   Yes    | —              | RS-485 device address (factory default is `2`)                     |
| `baudrate` | select |    No    | `19200`        | One of `9600`/`19200`/`38400`/`57600`/`115200` — Aurora default is 19200, 8N1 |
| `port`     | text   |    No    | `/dev/ttyUSB0` | Device path *inside the container* for the USB-to-RS485 adapter    |

`port` is a Linux USB-serial enumeration fact, not a property of this inverter model — it depends
entirely on that specific base station's own USB topology (how many other adapters are attached),
so it's a plain configurable field with a sane default, never hardcoded. The host app's own
container already has generic access to any USB-serial device (see "Docker device passthrough"
below) — nothing to configure at the container level, ever, per installation.

---

## Hardware Requirements

### RS-485 wiring

Connect the inverter's RS-485 terminals to a USB-to-RS485 adapter (CH340, PL2303, FTDI, or similar):

| Inverter terminal | Adapter terminal |
| ----------------- | ---------------- |
| A+                | A+               |
| B−                | B−               |
| GND               | GND (if present) |

Use shielded twisted-pair cable for runs longer than 5 m. Terminate with 120 Ω across A+/B− on long bus runs.

**Safety:** Turn off the inverter before opening the enclosure to access the RS-485 terminals.

### Docker device passthrough (Raspberry Pi)

Nothing to set up — the host app's `docker-compose.yml` already bind-mounts `/dev` and grants the
container `device_cgroup_rules` scoped to the `ttyUSB`/`ttyACM` device classes (kernel-fixed major
numbers, not specific paths). This is identical across every base station, decided once, and never
touched per-installation: no `docker-compose.override.yml`, no udev rule, no per-customer config at
the Docker level, ever. Just plug the adapter in and it's visible inside the container immediately
— no restart needed.

**If you have more than one USB-serial adapter** on the same Pi, both are equally visible; use
`ls /dev/ttyUSB*` (or `/dev/ttyACM*`) to see which is which and set this driver's **Serial Port**
field to the correct one. Enumeration order (which physical adapter becomes `ttyUSB0` vs `ttyUSB1`)
can shift across a reboot if you have more than one — if that matters for your setup, a udev rule
giving your adapter a stable symlink is still a legitimate option (`SUBSYSTEM=="tty",
ATTRS{idVendor}=="...", ATTRS{idProduct}=="...", SYMLINK+="..."`, find IDs with `lsusb`), and the
symlink shows up under `/dev` the same way any other device does since the whole directory is
bind-mounted — just set **Serial Port** to match. This is now purely about your own preference for
a stable name, not something required to make the container see the device at all.

---

## Protocol Details

The driver implements the **proprietary Aurora binary protocol** (Rev. 5.1) — not Modbus.

- **Frame size:** 10 bytes (8 data + 2 CRC)
- **CRC:** CRC-16/X-25 (poly = 0x8408, init = 0xFFFF, final complement)

### Commands used

| Command | Value  | Description                                          |
| ------- | ------ | ---------------------------------------------------- |
| DSP     | `0x3B` | Read a DSP measurement; returns IEEE-754 float       |
| Energy  | `0x4E` | Read a cumulated energy counter; returns uint32 (Wh) |

### DSP indices read

| Index | Description          | Unit |
| ----- | -------------------- | ---- |
| 1     | Grid voltage         | V    |
| 3     | AC output power      | W    |
| 4     | Grid frequency       | Hz   |
| 21    | Inverter temperature | °C   |
| 23    | DC input voltage     | V    |
| 25    | DC input current     | A    |

### Energy counter indices read

| Index | Description           | Unit |
| ----- | --------------------- | ---- |
| 0     | Energy produced today | Wh   |
| 4     | Lifetime total energy | Wh   |

---

## Data Mapping

| Contract field      | Source                       |
| ------------------- | ---------------------------- |
| `solar_power_w`     | DSP index 3 (clamped to ≥ 0) |
| `daily_energy_wh`   | Energy counter index 0       |
| `total_energy_wh`   | Energy counter index 4       |
| `temperature_c`     | DSP index 21                 |
| `dc_voltage_v`      | DSP index 23                 |
| `dc_current_a`      | DSP index 25                 |
| `grid_voltage_v`    | DSP index 1                  |
| `grid_frequency_hz` | DSP index 4                  |

---

## Night / Sleep Behaviour

Aurora inverters shut down the RS-485 communication board at night (no standby power). When the inverter does not respond, the driver returns `None`. The app:

- Records `solar_power_w = 0.0` for that snapshot
- Shows **sleeping** status on the device card
- Does not treat it as an error

---

## Discovery

`discover()` tries a small set of candidate serial ports in order (`_PROBE_PORTS`: `/dev/ttyUSB0`–`/dev/ttyUSB3`, then `/dev/ttyACM0`–`/dev/ttyACM3`), covering the common single-adapter case plus a second/third adapter on either device class a chipset might enumerate under (`ttyUSB` for vendor-specific USB-serial chips like FTDI/CH340/PL2303/CP210x, `ttyACM` for the USB-IF's standard CDC-ACM class). A candidate that doesn't exist fails instantly and costs virtually nothing to skip, so both classes get the same range; discovery stops at the first candidate that actually yields an inverter, since a customer's bus is wired to exactly one adapter.

For whichever port turns out to exist, the driver scans RS-485 addresses 1–10 sequentially using the 19200-baud default, then retries with other common baud rates (9600, 38400, 57600, 115200) if nothing responds. A valid response is determined by a correct CRC-16 checksum — the response byte 0 is the inverter's alarm state, not an address echo. Discovery is limited to addresses 1–10 to complete within a reasonable time.

---

## Thread Safety

All serial I/O is protected by a threading lock, making concurrent calls from the APScheduler background tasks safe.

---

## Troubleshooting

### Port not found

```bash
# Linux — confirm USB adapter is detected
lsusb | grep -i "CH340\|PL2303\|FTDI"
ls -l /dev/ttyUSB*

# Windows — check Device Manager › Ports (COM & LPT)
```

### Permission denied (Linux)

```bash
sudo usermod -aG dialout $USER
# Log out and back in
```

### No response from inverter

1. **Check wiring** — A+ must connect to A+, B− to B−. RS-485 polarity labelling is inconsistent; try swapping A and B if there is no response.
2. **Check address** — the factory default is `2`, but verify in the inverter's setup menu. Multiple inverters on one bus each need a unique address.
3. **Check baud rate** — default is 19200; older firmware may use 9600.
4. **Inverter must be producing** — the communication board is only powered during daylight hours.

### CRC mismatch errors

- Indicates electrical noise, typically on long cable runs.
- Try a shorter cable, add 120 Ω termination resistors, or switch to shielded cable.
- Reduce baud rate to 9600 if errors persist.
