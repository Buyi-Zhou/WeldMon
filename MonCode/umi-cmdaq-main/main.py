import spidev
import RPi.GPIO as GPIO
import time

# Configuration
GEN_GPIO_IN_1_PIN = 29         # GPIO pin connected to MCU's GEN_GPIO_IN_1 signal
GEN_GPIO_IN_2_PIN = 31
SPI_BUS = 0                      # SPI bus number (commonly 0 on RPi)
SPI_DEVICE = 0                   # SPI device number (commonly 0 on RPi)
CHUNK_SIZE = 4096                # Number of bytes to read in each chunk
OUTPUT_FILENAME = "received_file.bin"

# Setup GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(GEN_GPIO_IN_1_PIN, GPIO.IN)
GPIO.setup(GEN_GPIO_IN_2_PIN, GPIO.IN)

# Open SPI bus
spi = spidev.SpiDev()
spi.open(0, 0)  # (bus, device)
spi.max_speed_hz = 10000000
spi.mode = 0b00  # Match CPOL and CPHA of STM32



def wait_for_gpio(pin, state=GPIO.HIGH, timeout=10):
    """
    Wait for a GPIO pin to reach a specific state.
    """
    start_time = time.time()
    while GPIO.input(pin) != state:
        if (time.time() - start_time) > timeout:
            raise TimeoutError(f"Timeout waiting for GPIO pin {pin} to reach state {state}.")
        time.sleep(0.001)  # 1ms delay to reduce CPU usage
    return


def receive_chunk(chunk_size):
    # Send dummy bytes to receive the data chunk
    chunk = spi.xfer2([0x00]*chunk_size)
    if len(chunk) < chunk_size:
        raise ValueError("Incomplete data received from MCU.")
    return chunk


def receive_data():

    send_filename("testing.bin", 0x03)

    # Wait for GPIO1 high to confirm that transfer has been armed
    print("Waiting for GPIO1 high (Transfer Armed)...")
    wait_for_gpio(GEN_GPIO_IN_1_PIN, GPIO.HIGH, timeout=5)
    print("Transfer Armed. Starting data reception...")

    # Open the output file
    counter = 0
    with open(OUTPUT_FILENAME, "wb") as f:
        while True:
            # Wait for GPIO2 high to indicate MCU is ready to send a chunk
            try:
                wait_for_gpio(GEN_GPIO_IN_2_PIN, GPIO.HIGH, timeout=10)  # Adjust timeout as needed
            except TimeoutError:
                print("No more chunks to receive or timeout occurred.")
                break  # Exit the loop if no more chunks are expected

            # Receive the chunk
            chunk = receive_chunk(CHUNK_SIZE)
            f.write(bytearray(chunk))
            counter += len(chunk)
            print(f"Chunk received: {counter}")

    print(f"\nData reception completed. File saved as {OUTPUT_FILENAME}.")


# Send command and read response
def send_command(command):
    # Send command and read response
    response = spi.xfer2(command + [0x00] * 18)  # Sending command + receiving response
    # response = spi.xfer2([0x00])

    return response  # Ignore echo of command byte


def send_filename(filename, control_byte):
    """
    Sends a filename to the STM32 over SPI and waits for a response.
    """
    if len(filename) > 19:
        raise ValueError("Filename must be 19 characters or less")

    # Prepare the data
    log_stat_byte = 0x01
    filename_bytes = filename.encode('ascii')  # Convert to ASCII
    padded_filename = filename_bytes + b'\x00' * (18 - len(filename_bytes))  # Pad to 19 bytes

    print(len(padded_filename))

    data_to_send = [control_byte] + [log_stat_byte] + list(padded_filename)

    # Send the filename
    print("Sending filename...")
    response = spi.xfer2(data_to_send)  # Send data to STM32 (full-duplex SPI)

    return response

while True:
    # Example: Send command 0x01 and read response
    # response = send_command(0x7F)
    # response = send_command([0x02, 0x02])
    # response = send_filename("testing.bin", 0x02)
    receive_data()
    break