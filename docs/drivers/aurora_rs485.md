# Aurora Power-One — PV Inverter Driver Reference

**Driver ID:** `aurora_rs485`
**Device type:** `pv_inverter`
**Connection:** RS-485 serial (or TCP socket bridge)
**Manufacturer:** Power-One (ABB)
**Supported models:** Aurora PVI grid-tied series, Aurora UNO single-phase; ABB-branded Aurora models (same protocol after acquisition)

> For the data contract this driver must satisfy, see [pv_inverter.md](../contracts/pv_inverter.md).

---

## Configuration

| Field      | Type   | Required | Default | Description                                                        |
| ---------- | ------ | :------: | ------- | ------------------------------------------------------------------ |
| `address`  | number |   Yes    | —       | RS-485 device address (factory default is `2`)                     |
| `baudrate` | select |    No    | `19200` | One of `9600`/`19200`/`38400`/`57600`/`115200` — Aurora default is 19200, 8N1 |

The serial port is fixed at `/dev/ttyUSB1` inside the container. Use the udev symlink approach (see hardware setup below) to map any adapter to that path.

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

Pass the USB adapter into the container via `docker-compose.override.yml`:

```yaml
services:
  energy-optimizer:
    devices:
      - /dev/aurora:/dev/ttyUSB1
```

Create a stable udev symlink so the device name survives reboots:

```bash
# /etc/udev/rules.d/99-aurora.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="aurora"
```

Reload: `sudo udevadm control --reload-rules && sudo udevadm trigger`

> The repo's checked-in `docker-compose.override.yml` currently maps the raw device node
> (`/dev/ttyUSB0:/dev/ttyUSB1`) rather than this symlink, which reintroduces the reboot-renumbering
> risk the symlink avoids. See `docs/RASPBERRY_PI_DEPLOYMENT.md`'s RS-485 section for the trade-off
> and how to switch to the symlink-based mapping shown above.

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

The driver scans RS-485 addresses 1–10 sequentially using the 19200-baud default, then retries with other common baud rates (9600, 38400, 57600, 115200) if nothing responds. A valid response is determined by a correct CRC-16 checksum — the response byte 0 is the inverter's alarm state, not an address echo. Discovery is limited to addresses 1–10 to complete within a reasonable time (~30 s maximum).

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
