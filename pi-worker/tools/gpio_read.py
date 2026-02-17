"""
JARVIS Pi Tool — GPIO Read
Read the value of a GPIO pin using libgpiod (gpiod).
"""
import gpiod


def run(args: dict) -> dict:
    """Read a GPIO pin value.

    Args:
        pin (int): GPIO BCM pin number
        _config.allowed_pins (list): Enforced pin allowlist
    """
    pin = args.get("pin")
    if pin is None:
        raise ValueError("Missing required arg: pin")

    pin = int(pin)
    allowed = args.get("_config", {}).get("allowed_pins", [])
    if allowed and pin not in allowed:
        raise PermissionError(f"GPIO pin {pin} not in allowlist {allowed}")

    chip = gpiod.Chip("gpiochip4")  # Pi 5 uses gpiochip4
    line = chip.get_line(pin)
    line.request(consumer="jarvis", type=gpiod.LINE_REQ_DIR_IN)
    value = line.get_value()
    line.release()
    chip.close()

    return {
        "stdout": str(value),
        "data": {"pin": pin, "value": value},
    }
