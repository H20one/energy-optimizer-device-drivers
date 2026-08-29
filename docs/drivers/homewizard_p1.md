# HomeWizard P1 Dongle — Grid Meter Driver Reference

**Driver ID:** `homewizard_p1`
**Device type:** `grid_meter`
**Connection:** WiFi (HTTP)
**Manufacturer:** HomeWizard
**Supported models:** HomeWizard Energy P1 Dongle (HWE-P1), local API v1

> For the data contract this driver must satisfy, see [grid_meter.md](../contracts/grid_meter.md).

---

## Configuration

| Field | Type | Required | Default | Description               |
| ----- | ---- | :------: | ------- | -------------------------- |
| `ip`  | text |   Yes    | —       | IP address of the dongle  |

The local API must be **enabled** in the HomeWizard Energy app before the driver can connect (see Setup below).

> **Advanced override:** a `timeout` key (HTTP request timeout in seconds, default `10`) can be
> written directly to the stored device config. It's not exposed in the setup UI — `config_schema()`
> only surfaces `ip`.

---

## Prerequisites

1. Plug the P1 dongle into the **P1 port** (RJ12) on your smart meter.
2. Connect the dongle to your WiFi network using the **HomeWizard Energy** app.
3. In the app: tap the dongle → Settings (⚙) → enable **Local API**.
4. Note the IP address shown in the same settings screen, or find it in your router's DHCP client list.

---

## How the Driver Communicates

The driver polls a single endpoint every 5 seconds:

```
GET http://<ip>/api/v1/data
```

No authentication is required. Example response:

```json
{
  "active_power_w": -450,
  "active_power_l1_w": -200,
  "active_power_l2_w": -150,
  "active_power_l3_w": -100,
  "total_power_import_t1_kwh": 1234.56,
  "total_power_import_t2_kwh": 567.89,
  "total_power_export_t1_kwh": 789.01,
  "total_power_export_t2_kwh": 234.56,
  "total_gas_m3": 1234.567,
  "active_voltage_l1_v": 230.1,
  "active_voltage_l2_v": 231.2,
  "active_voltage_l3_v": 229.8,
  "active_current_l1_a": 1.2,
  "active_current_l2_a": 0.8,
  "active_current_l3_a": 0.5,
  "active_frequency_hz": 50.01
}
```

A negative `active_power_w` means the household is **exporting** to the grid.

---

## Data Mapping

| Contract field     | Source API field                                               |
| ------------------ | -------------------------------------------------------------- |
| `grid_power_w`     | `active_power_w` (sum of per-phase values if field is missing) |
| `import_total_kwh` | `total_power_import_t1_kwh + total_power_import_t2_kwh`        |
| `export_total_kwh` | `total_power_export_t1_kwh + total_power_export_t2_kwh`        |
| `import_t1_kwh`    | `total_power_import_t1_kwh`                                    |
| `import_t2_kwh`    | `total_power_import_t2_kwh`                                    |
| `export_t1_kwh`    | `total_power_export_t1_kwh`                                    |
| `export_t2_kwh`    | `total_power_export_t2_kwh`                                    |
| `gas_total_m3`     | `total_gas_m3` (`None` if not in response)                     |
| `voltage_l1_v`     | `active_voltage_l1_v`                                          |
| `voltage_l2_v`     | `active_voltage_l2_v`                                          |
| `voltage_l3_v`     | `active_voltage_l3_v`                                          |
| `current_l1_a`     | `active_current_l1_a`                                          |
| `current_l2_a`     | `active_current_l2_a`                                          |
| `current_l3_a`     | `active_current_l3_a`                                          |
| `frequency_hz`     | `active_frequency_hz`                                          |

---

## Discovery

The driver scans all 253 host addresses on the local `/24` subnet (derived from the Energy
Optimizer's own IP) using a thread pool, probing `GET http://<ip>/api` (not the `/api/v1/data`
data endpoint) with a short timeout. Any host that responds with `product_type` equal to
`HWE-P1`, `HWE-SKT`, or `HWE-WTR` is returned as a discovered device — this means HomeWizard
sockets and water meters answering the same probe are matched too, not only P1 dongles. Devices
with the local API disabled will not appear.

---

## Troubleshooting

### No data / connection errors

1. Confirm the dongle LED is solid green or blue (WiFi connected).
2. Test the API directly: `curl http://<ip>/api/v1/data`
3. Verify the **Local API** is enabled in the HomeWizard Energy app.
4. Check the IP address is correct.
5. Ensure no firewall blocks port 80 between the Energy Optimizer and the dongle.

### Stale data

The P1 port updates every ~1 second. If data appears delayed:

- Check WiFi signal strength to the dongle (the HomeWizard app shows signal quality).
- Consider moving the router or using a WiFi extender if the meter is far from the access point.

### References

- [HomeWizard Energy Local API Documentation](https://homewizard-energy-api.readthedocs.io/)
