#!/usr/bin/env python3
"""
scripts/pi_diagnostics.py — AgentEdge Raspberry Pi 4 Hardware Diagnostics
==========================================================================
Run on the Pi BEFORE starting hybrid mode to verify every hardware component.

Usage (on Raspberry Pi 4):
  source .venv/bin/activate
  python scripts/pi_diagnostics.py

Expected output when everything is correctly wired:
  [PASS] smbus2 available
  [PASS] RPi.GPIO available
  [PASS] I2C bus 1 accessible
  [PASS] MPU6050 found at 0x68
  [PASS] MPU6050 live readings sensible
  [PASS] GPIO BCM23 (buzzer) toggles without error
  [PASS] GPIO BCM24 (led_red) toggles without error
  ...

⚠️  This script DOES produce brief physical GPIO pulses (< 200 ms).
    Ensure LEDs have current-limiting resistors and the buzzer is safe.
"""
import os
import sys
import time

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── smbus2 ────────────────────────────────────────────────────────────────────
try:
    import smbus2
    print(f"{PASS} smbus2 available (version not tracked by pip)")
except ImportError:
    print(f"{FAIL} smbus2 not installed. Run: pip install smbus2")
    smbus2 = None

# ── RPi.GPIO ─────────────────────────────────────────────────────────────────
try:
    import RPi.GPIO as GPIO
    print(f"{PASS} RPi.GPIO available")
except ImportError:
    print(f"{FAIL} RPi.GPIO not installed. Run: pip install RPi.GPIO")
    GPIO = None
except RuntimeError as e:
    print(f"{WARN} RPi.GPIO import warning (possibly not on Pi): {e}")
    GPIO = None

# ── I2C bus 1 ─────────────────────────────────────────────────────────────────
i2c_ok = False
if smbus2:
    try:
        bus = smbus2.SMBus(1)
        bus.close()
        print(f"{PASS} I2C bus 1 accessible (/dev/i2c-1)")
        i2c_ok = True
    except FileNotFoundError:
        print(
            f"{FAIL} /dev/i2c-1 not found. Enable I2C:\n"
            f"       sudo raspi-config → Interface Options → I2C → Enable\n"
            f"       sudo reboot"
        )
    except Exception as exc:
        print(f"{FAIL} I2C bus 1 error: {exc}")
else:
    print(f"{WARN} Skipping I2C bus check (smbus2 not available)")

# ── Scan I2C bus for devices ──────────────────────────────────────────────────
if smbus2 and i2c_ok:
    print("\n  I2C bus scan (addresses that respond):")
    found = []
    try:
        bus = smbus2.SMBus(1)
        for addr in range(3, 128):
            try:
                bus.read_byte(addr)
                found.append(f"0x{addr:02X}")
            except OSError:
                pass
        bus.close()
        if found:
            print(f"    Devices: {' '.join(found)}")
            if "0x68" in found:
                print(f"    → MPU6050 at 0x68 detected ✓")
            elif "0x69" in found:
                print(f"    → MPU6050 at 0x69 detected (AD0 pin pulled HIGH)")
            else:
                print(f"{WARN} MPU6050 not found. Check: VCC→3.3V, GND, SDA→GPIO2(pin3), SCL→GPIO3(pin5)")
        else:
            print(f"{WARN} No I2C devices found — check wiring")
    except Exception as exc:
        print(f"{FAIL} I2C scan error: {exc}")

# ── MPU6050 who_am_i ─────────────────────────────────────────────────────────
mpu_ok = False
if smbus2 and i2c_ok:
    print()
    for addr in (0x68, 0x69):
        try:
            bus = smbus2.SMBus(1)
            who = bus.read_byte_data(addr, 0x75)
            bus.close()
            if who == 0x68:
                print(f"{PASS} MPU6050 WHO_AM_I=0x{who:02X} at 0x{addr:02X} ✓")
                mpu_ok = True
            else:
                print(f"{WARN} Device at 0x{addr:02X}: WHO_AM_I=0x{who:02X} (expected 0x68)")
        except OSError:
            pass   # not at this address
        except Exception as exc:
            print(f"{FAIL} Error reading WHO_AM_I at 0x{addr:02X}: {exc}")

if not mpu_ok:
    print(f"{FAIL} MPU6050 not found or WHO_AM_I mismatch")

# ── MPU6050 live accelerometer readings ───────────────────────────────────────
if smbus2 and i2c_ok and mpu_ok:
    print()
    try:
        bus = smbus2.SMBus(1)
        # Wake sensor
        bus.write_byte_data(0x68, 0x6B, 0x00)
        time.sleep(0.1)

        def read16s(reg):
            h = bus.read_byte_data(0x68, reg)
            l = bus.read_byte_data(0x68, reg + 1)
            v = (h << 8) | l
            return v - 65536 if v >= 32768 else v

        ax = read16s(0x3B) / 16384.0
        ay = read16s(0x3D) / 16384.0
        az = read16s(0x3F) / 16384.0
        mag = (ax**2 + ay**2 + az**2) ** 0.5
        bus.close()

        print(f"{PASS} MPU6050 live data: ax={ax:.3f}g  ay={ay:.3f}g  az={az:.3f}g  |total|={mag:.3f}g")

        if 0.5 <= mag <= 1.5:
            print(f"      Total magnitude {mag:.3f} g is consistent with gravity on a stationary board ✓")
        else:
            print(f"{WARN} Total magnitude {mag:.3f} g is outside expected 0.5–1.5 g range — check power supply or mounting")

    except Exception as exc:
        print(f"{FAIL} MPU6050 live read error: {exc}")

# ── GPIO pin test ─────────────────────────────────────────────────────────────
if GPIO:
    print()
    GPIO_MAP = {
        "buzzer":  23,
        "led_red": 24,
        "led_grn": 25,
        "led_yel": 12,
    }
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for name, pin in GPIO_MAP.items():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            GPIO.output(pin, GPIO.HIGH)
            time.sleep(0.15)
            GPIO.output(pin, GPIO.LOW)
            print(f"{PASS} GPIO BCM{pin} ({name}) toggled HIGH→LOW")
        GPIO.cleanup()
    except Exception as exc:
        print(f"{FAIL} GPIO test error: {exc}")
        try:
            GPIO.cleanup()
        except Exception:
            pass
else:
    print(f"{WARN} Skipping GPIO test (RPi.GPIO not available)")

# ── Python version ────────────────────────────────────────────────────────────
print()
import platform
pv = platform.python_version()
print(f"  Python {pv} on {platform.machine()}")
if sys.version_info[:2] >= (3, 11):
    print(f"{PASS} Python version compatible")
else:
    print(f"{WARN} Python < 3.11 — some type hints may not work")

print()
print("Diagnostics complete. Review any [FAIL] or [WARN] lines above.")
print("When all checks pass, run: python main.py demo --mode hybrid")
