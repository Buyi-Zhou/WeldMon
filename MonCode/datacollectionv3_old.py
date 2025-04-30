from daqhats import mcc128, OptionFlags, HatIDs, HatError, AnalogInputMode, AnalogInputRange
from daqhats_utils import select_hat_device, chan_list_to_mask
import wave
import time
import os
from datetime import datetime
from datetime import timedelta
from sys import stdout
from time import sleep
import gpiod
from influxdb_client import InfluxDBClient
import logging
import requests

# Constants
SAMPLE_RATE = 44100  # 44.1 kHz
DATA_FOLDER = 'data'
AR_PIN = 23  # Pin connected to the AR of the MAX9814
CHIP = 'gpiochip0'  # Use 'gpiochip0' for Raspberry Pi 4 and later

# InfluxDB 2.0 Settings
INFLUXDB_URL = "http://192.168.0.246:8086"
INFLUXDB_TOKEN = "p9nbrQ9N3yv7ys_G3vgBH42Z7PDn8sQjogZXPg7JUpDjP3rE3OfzCu5McL5l7B-X0E5VsKc-LTiV7sjW5IG_-A=="
INFLUXDB_ORG = "Heungkuk"
INFLUXDB_BUCKET = "PLCData"

# Setup logging
logging.basicConfig(filename='audio_recording.log', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# Setup GPIO using gpiod
chip = gpiod.Chip(CHIP)
ar_line = chip.get_line(AR_PIN)
ar_line.request(consumer="AR_PIN", type=gpiod.LINE_REQ_DIR_OUT)

READ_ALL_AVAILABLE = -1

# InfluxDB client setup
client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
query_api = client.query_api()

channels = [0]  # Assuming we are only using channel 0 for WAV file recording
channel_mask = chan_list_to_mask(channels)
num_channels = len(channels)

input_mode = AnalogInputMode.SE
input_range = AnalogInputRange.BIP_10V

samples_per_channel = 0
options = OptionFlags.CONTINUOUS
hat = None

# Global variable to track the last time the welder current dropped below 200
last_below_200_time = None
# Variable to track the last known state of the welder current
last_state = None

def create_wave_file(tip_number, parts_welded, time_since_last_weld, timestamp):
    """Create a new wave file with a unique timestamp-based name in the data folder."""
    # Add 9 hours to the GMT timestamp to convert it to KST
    kst_time = timestamp + timedelta(hours=9)
    date_str = kst_time.strftime('%Y%m%d')  # Extract date in YYYYMMDD format
    timestamp_str = kst_time.strftime('%Y%m%d-%H%M%S')  # Full timestamp

    # Create a directory for the date if it doesn't exist
    date_folder = os.path.join(DATA_FOLDER, date_str)
    if not os.path.exists(date_folder):
        os.makedirs(date_folder)

    # Create the filename and file path
    filename = os.path.join(date_folder, f'{timestamp_str}-tip{tip_number}-{parts_welded}-{time_since_last_weld:.2f}s.wav')
    
    # Open the wave file
    wave_file = wave.open(filename, 'wb')
    wave_file.setnchannels(1)  # Mono
    wave_file.setsampwidth(2)  # 16-bit samples
    wave_file.setframerate(SAMPLE_RATE)  # 44.1kHz sample rate

    return wave_file, filename


def fetch_influxdb_data():
    """Fetch data from the InfluxDB 2.x and return the latest time."""
    try:
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1m)
          |> filter(fn: (r) => r["_measurement"] == "plc_data")
          |> filter(fn: (r) => r["_field"] == "Welder_1_current" or r["_field"] == "Welder_1_-_No._of_tips" or r["_field"] == "Welder_1_tip_usage")
          |> last()
        '''

        tables = query_api.query(query=query, org=INFLUXDB_ORG)

        welder_current = None
        tip_number = None
        parts_welded = None
        timestamp = None

        for table in tables:
            for record in table.records:
                if record.get_field() == "Welder_1_current":
                    welder_current = record.get_value()
                    timestamp = record.get_time()  # Get the timestamp from the record
                elif record.get_field() == "Welder_1_-_No._of_tips":
                    tip_number = record.get_value()
                elif record.get_field() == "Welder_1_tip_usage":
                    parts_welded = record.get_value()

        return welder_current, tip_number, parts_welded, timestamp
    except Exception as e:
        logging.error(f"Error fetching data from InfluxDB: {e}")
        return None, None, None, None

def record_audio(hat, num_channels, tip_number, parts_welded, time_since_last_weld, timestamp):
    """Record audio for 20 seconds."""
    wave_file, filename = create_wave_file(tip_number, parts_welded, time_since_last_weld, timestamp)
    print(f'Recording started: {filename}')
    start_time = time.time()
    
    try:
        read_request_size = READ_ALL_AVAILABLE
        timeout = 5.0

        # Set AR_PIN high to enable AGC reset
        ar_line.set_value(1)

        # Initialize the hat
        hat.a_in_scan_start(channel_mask, samples_per_channel, SAMPLE_RATE, options)

        while time.time() - start_time < 20:
            read_result = hat.a_in_scan_read(read_request_size, timeout)

            if read_result.hardware_overrun:
                print('\n\nHardware overrun\n')
                break
            elif read_result.buffer_overrun:
                print('\n\nBuffer overrun\n')
                break

            for sample in read_result.data:
                int_value = int((sample / 10.0) * 32767.0)
                wave_file.writeframesraw(int_value.to_bytes(2, byteorder='little', signed=True))

            print(f'Samples Read: {len(read_result.data)}', end='\r')
            stdout.flush()
            sleep(0.1)

    except KeyboardInterrupt:
        print(f'\nStopping\n')
    finally:
        ar_line.set_value(0)  # Set AR_PIN low to disable AGC reset
        wave_file.close()
        duration = time.time() - start_time
        print(f'Recording stopped: {filename}')
        print(f'Duration: {duration:.2f} seconds')

        hat.a_in_scan_stop()
        hat.a_in_scan_cleanup()

	# Add the following line to send the file to the server after recording
        send_file_to_server(filename)


def send_file_to_server(filename):
    """Send the recorded audio file to the server via HTTP POST and delete it locally if successful."""
    url = "http://192.168.0.246:8081/upload"  # Replace with your server's IP and port
    try:
        with open(filename, 'rb') as file:
            files = {'file': file}
            response = requests.post(url, files=files)
            if response.status_code == 200:
                print(f"File {filename} successfully sent to server.")
                os.remove(filename)  # Delete the file after successful upload
                print(f"File {filename} deleted locally.")
            else:
                print(f"Failed to send file {filename} to server. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error sending file {filename} to server: {e}")
        logging.error(f"Error sending file {filename} to server: {e}")

def monitor_welder_current(hat, num_channels):
    """Monitor Welder_1_current value and trigger recording based on conditions."""
    global last_below_200_time, last_state

    while True:
        welder_current, tip_number, parts_welded, timestamp = fetch_influxdb_data()

        if welder_current is None:
            print("Error fetching data, retrying in 1 minute.")
            sleep(60)
            continue

        current_state = 'above_200' if welder_current >= 200 else 'below_200'

        if last_state == 'above_200' and current_state == 'below_200':
            print("Welder current dropped below 200, updating last_below_200_time")
            last_below_200_time = time.time()

        if current_state == 'above_200':
            if last_state == 'below_200':
                print("Welder current went above 200, starting recording...")

                if last_below_200_time is not None:
                    time_since_last_weld = time.time() - last_below_200_time
                else:
                    time_since_last_weld = 0

                record_audio(hat, num_channels, tip_number, parts_welded, time_since_last_weld, timestamp)

            print("Monitoring welder current...")

        last_state = current_state
        sleep(1)

def main():
    hat = None  # Initialize hat to None

    try:
        address = select_hat_device(HatIDs.MCC_128)
        hat = mcc128(address)

        hat.a_in_mode_write(input_mode)
        hat.a_in_range_write(input_range)

        print('\nSelected MCC 128 HAT device at address', address)

        actual_scan_rate = hat.a_in_scan_actual_rate(num_channels, SAMPLE_RATE)
        print('Actual scan rate:', actual_scan_rate)

        while True:
            monitor_welder_current(hat, num_channels)

    except (HatError, ValueError) as err:
        print('\n', err)
    finally:
        if hat:  # Only attempt to stop/cleanup if hat was successfully initialized
            hat.a_in_scan_stop()
            hat.a_in_scan_cleanup()
        chip.close()

if __name__ == '__main__':
    main()
