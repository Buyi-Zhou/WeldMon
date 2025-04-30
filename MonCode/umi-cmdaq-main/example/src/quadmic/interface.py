import spidev
import serial
import RPi.GPIO as GPIO
import time
import sys
import threading
from enum import Enum, auto


class SMState(Enum):
    """
    Enum to monitor the states of the Sensor Module
    """
    INITIALIZING = auto()
    IDLE = auto()
    ERROR = auto()
    LOGGING = auto()
    DOWNLOADING = auto()
    STREAMING = auto()
    SHUTDOWN = auto()


class QuadMicError(Exception):
    """
    Base class for QuadMic-related exceptions.
    """
    def __init__(self, message):
        super().__init__(f"QuadMic Error: {message}")


class QuadMicDeviceBusy(QuadMicError):
    """
    Raised when the device is busy and cannot process a request.
    """
    def __init__(self):
        super().__init__("Device is busy. Please wait until the current operation finishes.")


class QuadMicDeviceNotInitialized(QuadMicError):
    """
    Raised when trying to access the device before initialization.
    """
    def __init__(self):
        super().__init__("Device is not initialized. Please initialize the device first.")


class QuadMicNoSDCard(QuadMicError):
    """
    Raised when SD Card related functions are called with no sd card on device
    """

    def __init__(self):
        super().__init__("Device does not have SD Card installed.")


class QuadMic:

    def __init__(self, uart_port="/dev/serial0", baud_rate=115200, spi_bus=0, spi_device=0, spi_mode=0b00, spi_speed=1000000, timeout=1):
        """
        Initialize the QuadMic interface using UART for commands and SPI for data reception.
        :param uart_port: UART port (e.g., /dev/serial0 for Raspberry Pi)
        :param baud_rate: Baud rate for UART communication
        :param spi_bus: SPI bus number
        :param spi_device: SPI device number
        :param spi_mode: SPI mode (0-3)
        :param spi_speed: SPI speed in Hz
        :param timeout: UART timeout for read operations
        """
        self.state = SMState.INITIALIZING

        # UART Settings
        self.uart_port = uart_port
        self.baud_rate = baud_rate
        self.timeout = timeout

        # SPI Settings
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.spi_mode = spi_mode
        self.spi_speed = spi_speed

        # GPIO Pins
        self.pin_29 = 29  # GPIO input for device busy state
        self.pin_31 = 31  # GPIO input for data ready signal
        self.pin_reset = 3  # GPIO pin to reset the device

        # Serial and SPI objects
        self.serial_conn = None
        self.spi = None
        self.streaming_thread = None
        self.streaming_active = threading.Event()

    def initialize(self):
        """Initialize UART, SPI, and GPIO."""
        try:
            # Initialize UART
            self.serial_conn = serial.Serial(
                self.uart_port, self.baud_rate, timeout=self.timeout
            )

            # Initialize SPI
            self.spi = spidev.SpiDev()
            self.spi.open(self.spi_bus, self.spi_device)
            self.spi.max_speed_hz = self.spi_speed
            self.spi.mode = self.spi_mode
            self.spi.lsbfirst = False

            # Configure GPIO
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(self.pin_29, GPIO.IN)
            GPIO.setup(self.pin_31, GPIO.IN)
            GPIO.setup(self.pin_reset, GPIO.OUT)

            self.state = SMState.IDLE
            print("QuadMic Initialized: UART for Commands, SPI for Data Reception.")

        except Exception as e:
            self.state = SMState.ERROR
            raise RuntimeError(f"Initialization failed: {e}")

    def reset_device(self):
        """Reset the device using the reset pin."""
        GPIO.output(self.pin_reset, GPIO.LOW)
        time.sleep(0.1)
        GPIO.output(self.pin_reset, GPIO.HIGH)
        time.sleep(0.1)
        print("Device Reset Complete.")

    def is_device_busy(self):
        return GPIO.input(self.pin_29) == GPIO.HIGH

    def send_command(self, command: list):
        """
        Send a command over UART.
        :param command: List of bytes representing the command.
        :return: Response from the device (if any)
        """
        try:
            if self.serial_conn:
                self.serial_conn.write(command + [0x00] * (20 - len(command)))
                time.sleep(0.05)  # Allow some processing time
            else:
                raise RuntimeError("UART connection not initialized.")
        except Exception as e:
            self.state = SMState.ERROR
            raise RuntimeError(f"UART Communication Error: {e}")

    def get_device_state(self):
        """
        Get the device state over UART
        """
        if not self.is_device_busy():
            try:
                self.send_command([0x01, 0x01])

                # Wait for response
                response = self.serial_conn.read(20)
                if len(response) == 0:
                    raise RuntimeError("No response received from device.")

                return list(response)
            except Exception as e:
                self.state = SMState.ERROR
                raise RuntimeError(f"UART Communication Error: {e}")
        else:
            raise QuadMicDeviceBusy()

    def __send_filename(self, filename, control_byte):

        """
        Send a filename to the device over UART.
        :param filename: Name of the file to be logged/retrieved
        :param control_byte: Command control byte
        """
        if len(filename) > 18:
            raise ValueError("Filename is too long. Maximum length is 18 characters.")
        if not filename.endswith(".bin"):
            raise ValueError("Filename must end with .bin.")

        # Prepare the filename data
        log_stat_byte = 0x01
        filename_bytes = filename.encode("ascii")
        padded_filename = filename_bytes + b'\x00' * (18 - len(filename_bytes))

        # Send data
        data_to_send = bytes([control_byte, log_stat_byte]) + padded_filename
        self.serial_conn.write(data_to_send)

    def start_logging(self, file_name):

        """
        Start logging data to a file.
        :param file_name: Name of the log file.
        :return: True if successful, False otherwise.
        """
        if self.state == SMState.IDLE:

            # Get the device status
            current_device_state = self.get_device_state()
            if current_device_state[0]:
                raise QuadMicNoSDCard()


            if len(file_name.split(".")) == 2 and file_name.endswith(".bin"):
                self.__send_filename(file_name, 0x02)
                self.state = SMState.LOGGING
                print(f"Logging started: {file_name}")
                return True
        return False

    def start_streaming(self, save_location):

        """
        Start real-time data streaming over UART.
        """
        if self.state == SMState.IDLE:
            self.state = SMState.STREAMING
            self.send_command([0x04, 0x01])
            print("Streaming started.")

            self.streaming_active.set()
            self.streaming_thread = threading.Thread(target=self._streaming_loop, args=(save_location,))
            self.streaming_thread.start()

    def _streaming_loop(self, save_location):
        """
        Internal method that runs in a separate thread to handle streaming
        """
        try:
            with open(save_location, "wb") as f:
                while self.streaming_active.is_set():  # Check if streaming is active
                    try:
                        self.__wait_on_pin_state(self.pin_31, GPIO.HIGH, timeout=10)
                    except TimeoutError:
                        break

                    # Receive chunks over SPI
                    chunk = self.__receive_chunk(chunk_size=3200)
                    f.write(bytearray(chunk))

        except Exception as e:
            sys.stdout.write(f"Exception occurred in streaming: {e}")

        sys.stdout.write("Streaming is exiting....")

    def stop_streaming(self):
        """
        Stop real-time data streaming over SPI.
        """
        if self.state == SMState.STREAMING:

            # Send command for stopping
            self.send_command([0x04, 0x02])
            time.sleep(0.1)

            self.streaming_active.clear()
            if self.streaming_thread:
                self.streaming_thread.join()
            self.state = SMState.IDLE
            print("Streaming stopped.")
        else:
            sys.stdout.write("Streaming is not running, so it cannot be stopped.\n")

    def stop_logging(self):

        """
        Stop logging data.
        """
        if self.state == SMState.LOGGING:
            self.send_command([0x02, 0x02])
            self.state = SMState.IDLE
            print("Logging stopped.")
        else:
            sys.stdout.write("Logging is not running, so it cannot be stopped.\n")

    @staticmethod
    def check_pin_state(pin, pin_state=GPIO.HIGH):
        return GPIO.input(pin) == pin_state

    @staticmethod
    def __wait_on_pin_state(pin, pin_state=GPIO.HIGH, timeout=10):

        """Wait for a GPIO pin to reach a specified state within a timeout."""
        start_time = time.time()
        while GPIO.input(pin) != pin_state:
            if (time.time() - start_time) > timeout:
                raise TimeoutError(f"Timeout waiting for GPIO pin {pin} to reach state {pin_state}")
            time.sleep(0.001)
        return

    def __receive_chunk(self, chunk_size):

        # Send dummy bytes to receive the data chunk
        return self.spi.xfer2([0x00] * chunk_size)

    def retrieve_stored_data(self, filename_to_read, save_location):
        """
        Retrieve a stored data file.
        - Sends command over UART
        - Receives data over SPI
        """

        if self.state == SMState.IDLE:

            # Get the device status
            current_device_state = self.get_device_state()
            if current_device_state[0]:
                raise QuadMicNoSDCard()

            print(f"Sending retrieval command for {filename_to_read} over UART...")
            self.__send_filename(filename_to_read, 0x03)

            # Wait for GPIO1 HIGH to confirm the transfer is armed
            self.__wait_on_pin_state(self.pin_29, GPIO.HIGH, timeout=5)

            self.state = SMState.DOWNLOADING
            length = 0

            # Read the file over SPI
            print("Receiving file over SPI...")
            with open(save_location, "wb") as f:
                while True:
                    try:
                        self.__wait_on_pin_state(self.pin_31, GPIO.HIGH, timeout=10)
                    except TimeoutError:
                        break

                    # Receive chunks over SPI
                    chunk = self.__receive_chunk(chunk_size=4096)
                    f.write(bytearray(chunk))
                    length = length + len(chunk)
                    print(length)

            self.state = SMState.IDLE
            print(f"Data retrieval complete: {save_location}")
            return True
        else:
            return False

    def cleanup(self):
        """Cleanup UART, SPI, and GPIO resources."""
        if self.serial_conn:
            self.serial_conn.close()
        if self.spi:
            self.spi.close()
        GPIO.cleanup()

