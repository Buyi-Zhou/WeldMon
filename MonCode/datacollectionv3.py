import interface

import wave
import time
import os
from datetime import datetime
from sys import stdout
from time import sleep
import gpiod
from influxdb_client import InfluxDBClient
import logging

# Constants
SAMPLE_RATE = 44100  # 44.1 kHz
DATA_FOLDER = 'data'
AR_PIN = 23  # Pin connected to the AR of the MAX9814
CHIP = 'gpiochip0'  # Use 'gpiochip0' for Raspberry Pi 4 and later

# InfluxDB 2.0 Settings
INFLUXDB_URL = "http://192.168.0.246:8086"
INFLUXDB_TOKEN = "u5unT30HCfCABFD5PUMV4HAL-s0mgki8a14g6sUR-Y_keXs62ioOR_WJ4AF48Q7g1lsJyYLIjINLt0nqzwDURQ=="
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

def create_wave_file(tip_number, parts_welded):
    """Create a new wave file with a unique timestamp-based name in the data folder."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = os.path.join(DATA_FOLDER, f'{timestamp}-tip{tip_number}-{parts_welded}.wav')
    wave_file = wave.open(filename, 'wb')
    wave_file.setnchannels(1)  # Mono
    wave_file.setsampwidth(2)  # 16-bit samples
    wave_file.setframerate(SAMPLE_RATE)  # 44.1kHz sample rate
    return wave_file, filename

def fetch_influxdb_data():
    """Fetch data from the InfluxDB 2.x."""
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

        for table in tables:
            for record in table.records:
                if record.get_field() == "Welder_1_current":
                    welder_current = record.get_value()
                elif record.get_field() == "Welder_1_-_No._of_tips":
                    tip_number = record.get_value()
                elif record.get_field() == "Welder_1_tip_usage":
                    parts_welded = record.get_value()

        return welder_current, tip_number, parts_welded
    except Exception as e:
        logging.error(f"Error fetching data from InfluxDB: {e}")
        return None, None, None

def record_audio(hat, num_channels, tip_number, parts_welded):
    """Record audio for 20 seconds."""
    wave_file, filename = create_wave_file(tip_number, parts_welded)
    print(f'Recording started: {filename}')
    start_time = time.time()

    try:
        read_request_size = READ_ALL_AVAILABLE
        timeout = 5.0

        # Set AR_PIN high to enable AGC reset
        ar_line.set_value(1)

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

def monitor_welder_current(hat, num_channels):
    """Monitor Welder_1_current value and trigger recording based on conditions."""
    while True:
        welder_current, tip_number, parts_welded = fetch_influxdb_data()

        if welder_current is None:
            print("Error fetching data, retrying in 3 minutes.")
            sleep(3 * 60)
            continue

        initial_value = welder_current

        if initial_value < 200:
            print("Initial Welder_1_current is below 200, monitoring for 30 seconds...")
            for _ in range(30):
                welder_current, _, _ = fetch_influxdb_data()
                if welder_current >= 200:
                    print("Welder_1_current went above 200, starting recording...")
                    record_audio(hat, num_channels, tip_number, parts_welded)
                    break
                sleep(1)
            else:
                print("Welder_1_current did not go above 200, sleeping for 3 minutes.")
                sleep(3 * 60)

        else:
            print("Initial Welder_1_current is above 200, monitoring for 60 seconds...")
            previous_below_200 = False
            for _ in range(60):
                welder_current, _, _ = fetch_influxdb_data()
                if previous_below_200 and welder_current >= 200:
                    print("Welder_1_current went below 200 and then above 200, starting recording...")
                    record_audio(hat, num_channels, tip_number, parts_welded)
                    break
                if welder_current < 200:
                    previous_below_200 = True
                sleep(1)
            else:
                print("Welder_1_current did not go below 200 and back up, sleeping for 3 minutes.")
                sleep(3 * 60)

def main():
    channels = [0]  # Assuming we are only using channel 0 for WAV file recording
    channel_mask = chan_list_to_mask(channels)
    num_channels = len(channels)

    input_mode = AnalogInputMode.SE
    input_range = AnalogInputRange.BIP_10V

    samples_per_channel = 0
    options = OptionFlags.CONTINUOUS
    hat = None

    try:
        address = select_hat_device(HatIDs.MCC_128)
        hat = mcc128(address)

        hat.a_in_mode_write(input_mode)
        hat.a_in_range_write(input_range)

        print('\nSelected MCC 128 HAT device at address', address)

        actual_scan_rate = hat.a_in_scan_actual_rate(num_channels, SAMPLE_RATE)
        print('Actual scan rate:', actual_scan_rate)

        hat.a_in_scan_start(channel_mask, samples_per_channel, SAMPLE_RATE, options)

        while True:
            monitor_welder_current(hat, num_channels)

    except (HatError, ValueError) as err:
        print('\n', err)
    finally:
        if hat:
            hat.a_in_scan_stop()
            hat.a_in_scan_cleanup()
        chip.close()

if __name__ == '__main__':
    main()
