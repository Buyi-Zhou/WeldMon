from daqhats import mcc128, OptionFlags, HatIDs, HatError, AnalogInputMode, AnalogInputRange
from daqhats_utils import select_hat_device, chan_list_to_mask, input_mode_to_string, input_range_to_string, enum_mask_to_string
import wave
import time
import os
from datetime import datetime
from sys import stdout
from time import sleep
import gpiod

# Constants
SAMPLE_RATE = 44100  # 44.1 kHz
RECORDINGS_FOLDER = 'recordings'
AR_PIN = 23  # Pin connected to the AR of the MAX9814
CHIP = 'gpiochip0'  # Use 'gpiochip0' for Raspberry Pi 4 and later

# Setup GPIO using gpiod
chip = gpiod.Chip(CHIP)
ar_line = chip.get_line(AR_PIN)
ar_line.request(consumer="AR_PIN", type=gpiod.LINE_REQ_DIR_OUT)

READ_ALL_AVAILABLE = -1
CURSOR_BACK_2 = '\x1b[2D'
ERASE_TO_END_OF_LINE = '\x1b[0K'


def create_recordings_folder():
    """Create the recordings folder if it doesn't exist."""
    if not os.path.exists(RECORDINGS_FOLDER):
        os.makedirs(RECORDINGS_FOLDER)


def create_wave_file():
    """Create a new wave file with a unique timestamp-based name in the recording folder."""
    create_recordings_folder()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(RECORDINGS_FOLDER, f'audio_{timestamp}.wav')
    wave_file = wave.open(filename, 'wb')
    wave_file.setnchannels(1)  # Mono
    wave_file.setsampwidth(2)  # 16-bit samples
    wave_file.setframerate(SAMPLE_RATE)  # 44.1kHz sample rate
    return wave_file, filename


def record_audio(hat, num_channels):
    """Record audio until interrupted."""
    wave_file, filename = create_wave_file()
    print(f'Recording started: {filename}')
    start_time = time.time()

    try:
        read_request_size = READ_ALL_AVAILABLE
        timeout = 5.0
        data = []

        # Set AR_PIN high to enable AGC reset
        ar_line.set_value(1)

        while True:
            read_result = hat.a_in_scan_read(read_request_size, timeout)

            if read_result.hardware_overrun:
                print('\n\nHardware overrun\n')
                break
            elif read_result.buffer_overrun:
                print('\n\nBuffer overrun\n')
                break

            samples_read_per_channel = int(len(read_result.data) / num_channels)
            data.extend(read_result.data)

            # Convert to 16-bit PCM and write to file
            for sample in read_result.data:
                int_value = int((sample / 10.0) * 32767.0)
                wave_file.writeframesraw(int_value.to_bytes(2, byteorder='little', signed=True))

            print('\rSamples Read: ', len(data), end='')
            stdout.flush()
            sleep(0.1)

    except KeyboardInterrupt:
        print(f'\nStopping\n')
    finally:
        # Set AR_PIN low to disable AGC reset
        ar_line.set_value(0)
        wave_file.close()
        print(f'Recording stopped: {filename}')
        print(f'Duration: {time.time() - start_time:.2f} seconds')


def main():
    channels = [0]  # Assuming we are only using channel 0 for WAV file recording
    channel_mask = chan_list_to_mask(channels)
    num_channels = len(channels)

    input_mode = AnalogInputMode.SE
    input_range = AnalogInputRange.BIP_10V

    samples_per_channel = 0
    options = OptionFlags.CONTINUOUS
    scan_rate = SAMPLE_RATE
    hat = None

    try:
        # Select an MCC 128 HAT device to use.
        address = select_hat_device(HatIDs.MCC_128)
        hat = mcc128(address)

        hat.a_in_mode_write(input_mode)
        hat.a_in_range_write(input_range)

        print('\nSelected MCC 128 HAT device at address', address)

        actual_scan_rate = hat.a_in_scan_actual_rate(num_channels, scan_rate)

        print('\nMCC 128 continuous scan example')
        print('    Input mode: ', input_mode_to_string(input_mode))
        print('    Input range: ', input_range_to_string(input_range))
        print('    Channels: ', ', '.join([str(chan) for chan in channels]))
        print('    Requested scan rate: ', scan_rate)
        print('    Actual scan rate: ', actual_scan_rate)
        print('    Options: ', enum_mask_to_string(OptionFlags, options))

        input('\nPress ENTER to start recording...')

        hat.a_in_scan_start(channel_mask, samples_per_channel, scan_rate, options)
        print('Starting scan ... Press Ctrl-C to stop\n')

        record_audio(hat, num_channels)

    except (HatError, ValueError) as err:
        print('\n', err)
    finally:
        if hat:
            hat.a_in_scan_stop()
            hat.a_in_scan_cleanup()
        chip.close()


if __name__ == '__main__':
    main()
