from src.quadmic.interface import QuadMic
import time

def main():

    # Instantiate
    quadmic = QuadMic()

    try:
        # Initialize the device
        quadmic.initialize()
        print("Device initialized successfully.")

        # Reset the device
        quadmic.reset_device()
        print("Device reset successfully.")
        time.sleep(5)

        # Read the device state
        print(f"Current Device State: {quadmic.get_device_state()}")

        # Start the logging process
        quadmic.start_streaming("testStream.bin")
        time.sleep(5)
        quadmic.stop_streaming()

    except Exception as e:
        print(f"{e}")
    finally:
        # Cleanup resources
        print("Cleaning up resources...")
        quadmic.cleanup()
        print("Cleanup complete.")

if __name__ == "__main__":
    main()