"""
JARVIS Pi Tool — GPIO Write
Set the value of a GPIO pin using libgpiod (gpiod).
"""
import gpiod


def run(args: dict) -> dict:
    """Write a value to a GPIO pin.

    Args:
        pin (int): GPIO BCM pin number
        value (int): 0 or 1
        _config.allowed_pins (list): Enforced pin allowlist
    """
    pin = args.get("pin")
    value = args.get("value")
    if pin is None or value is None:
        raise ValueError("Missing required args: pin, value")

    pin = int(pin)
    value = int(value)
    if value not in (0, 1):
        raise ValueError(f"Value must be 0 or 1, got {value}")

    allowed = args.get("_config", {}).get("allowed_pins", [])
    if allowed and pin not in allowed:
        raise PermissionError(f"GPIO pin {pin} not in allowlist {allowed}")

    chip = gpiod.Chip("gpiochip4")  # Pi 5 uses gpiochip4
    line = chip.get_line(pin)
    line.request(consumer="jarvis", type=gpiod.LINE_REQ_DIR_OUT)
    line.set_value(value)
    line.release()
    chip.close()

    return {
        "stdout": f"Pin {pin} set to {value}",
        "data": {"pin": pin, "value": value},
    }
