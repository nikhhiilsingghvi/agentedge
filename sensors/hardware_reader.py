import time
from sensors.simulator import SensorReading, SensorStatus

MPU6050_ADDR  = 0x68
INA219_ADDR   = 0x40
I2C_BUS       = 1          
DHT_PIN       = 4           
TRIG_PIN      = 17          
ECHO_PIN      = 27          
MQ135_SPI_CS  = 8           

class HardwareSensorReader:
    def __init__(self):
        pass

    def read_vibration(self) -> SensorReading:
        # accel_x = self._read_word_2c(0x3B) / 16384.0
        # accel_y = self._read_word_2c(0x3D) / 16384.0
        # accel_z = self._read_word_2c(0x3F) / 16384.0
        # val = (accel_x**2 + accel_y**2 + accel_z**2) ** 0.5
        try:
            val = 0.0
            status = (SensorStatus.FAULT if val >= 3.0
                      else SensorStatus.WARN if val >= 1.5
                      else SensorStatus.OK)
            return SensorReading("mpu6050", "vibration", val, "g",
                                 status, time.time())
        except Exception as e:
            return SensorReading("mpu6050", "vibration", None, "g",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason=str(e))

    def read_temperature(self) -> SensorReading:
        try:
            temp = 0.0
            status = (SensorStatus.FAULT if temp >= 60.0
                      else SensorStatus.WARN if temp >= 45.0
                      else SensorStatus.OK)
            return SensorReading("dht22", "temperature", temp, "Celsius",
                                 status, time.time())
        except Exception as e:
            return SensorReading("dht22", "temperature", None, "Celsius",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason=str(e))

    def read_current(self) -> SensorReading:
        try:
            current_a = 0.0 
            status = (SensorStatus.FAULT if current_a >= 2.0
                      else SensorStatus.WARN if current_a >= 1.5
                      else SensorStatus.OK)
            return SensorReading("ina219", "current", current_a, "Amps",
                                 status, time.time())
        except Exception as e:
            return SensorReading("ina219", "current", None, "Amps",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason=str(e))

    def read_distance(self) -> SensorReading:
        try:
            distance = 30.0
            status = (SensorStatus.FAULT if distance < 10.0
                      else SensorStatus.OK)
            return SensorReading("hcsr04", "distance", distance, "cm",
                                 status, time.time())
        except Exception as e:
            return SensorReading("hcsr04", "distance", None, "cm",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason=str(e))

    def read_gas(self) -> SensorReading:
        try:
            ppm = 120.0
            status = (SensorStatus.FAULT if ppm >= 400.0
                      else SensorStatus.WARN if ppm >= 200.0
                      else SensorStatus.OK)
            return SensorReading("mq135", "gas", ppm, "ppm",
                                 status, time.time())
        except Exception as e:
            return SensorReading("mq135", "gas", None, "ppm",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason=str(e))

    def read_all(self) -> dict:
        return {
            "mpu6050": self.read_vibration(),
            "dht22":   self.read_temperature(),
            "ina219":  self.read_current(),
            "hcsr04":  self.read_distance(),
            "mq135":   self.read_gas(),
        }

    def inject_recovery(self):
        pass
