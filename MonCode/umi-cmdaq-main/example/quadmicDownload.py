from src.quadmic.interface import QuadMic
import time

def main():

    # Instantiate
    quadmic = QuadMic()

    try:
        # Initialize the device
        quadmic.initialize()
        print("Device initialized successfully.")

        # # Reset the device
        quadmic.reset_device()
        print("Device reset successfully.")
        time.sleep(5)

        # Download the file
        print("Receiving file....")
        quadmic.retrieve_stored_data("test09.bin", "test09.bin")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Cleanup resources
        print("Cleaning up resources...")
        quadmic.cleanup()
        print("Cleanup complete.")

if __name__ == "__main__":
    main()
