"""
JARVIS Pi Tool — I2C Bus Scan
Scan an I2C bus for connected devices using smbus2.
"""
import smbus2


def run(args: dict) -> dict:
    """Scan I2C bus for connected devices.

    Args:
        bus (int): I2C bus number (default: 1)
    """
    bus_num = int(args.get("bus", 1))

    try:
        bus = smbus2.SMBus(bus_num)
    except FileNotFoundError:
        raise FileNotFoundError(f"I2C bus {bus_num} not found. Is I2C enabled? (raspi-config)")
    except PermissionError:
        raise PermissionError(f"No permission to access I2C bus {bus_num}. Add user to i2c group.")

    devices = []
    for addr in range(0x03, 0x78):
        try:
            bus.read_byte(addr)
            devices.append(addr)
        except OSError:
            pass

    bus.close()

    hex_addrs = [f"0x{a:02x}" for a in devices]
    return {
        "stdout": f"Found {len(devices)} device(s) on bus {bus_num}: {', '.join(hex_addrs) or 'none'}",
        "data": {
            "bus": bus_num,
            "device_count": len(devices),
            "addresses": devices,
            "addresses_hex": hex_addrs,
        },
    }
