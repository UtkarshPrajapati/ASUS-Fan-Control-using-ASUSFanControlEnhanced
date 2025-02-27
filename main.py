import logging
from logging.handlers import RotatingFileHandler
import subprocess
import time

# Set up logging with RotatingFileHandler to limit log file size to 10KB
logger = logging.getLogger()
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('fan_control.log', maxBytes=5000, backupCount=1)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# Define constants for temperature and speed
LOW_TEMP = 20    # Temperature where fan speed is at minimum
HIGH_TEMP = 60   # Temperature where fan speed reaches maximum
MIN_SPEED = 10   # Minimum fan speed percentage
MAX_SPEED = 100  # Maximum fan speed percentage

def get_cpu_temp():
    """Gets the current CPU temperature using AsusFanControl.exe."""
    try:
        result = subprocess.run(
            ["AsusFanControl.exe", "--get-cpu-temp"],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.strip()
        temp_str = output.split(": ")[1]  # Extract temp value
        return int(temp_str)
    except subprocess.CalledProcessError as e:
        logging.error(f"Error getting CPU temperature: {e}")
        return None
    except IndexError:
        logging.error("Error parsing CPU temperature output.")
        return None
    except FileNotFoundError:
        logging.error("AsusFanControl.exe not found. Ensure it is in the system PATH.")
        return None

def get_current_fan_speeds():
    """Gets the current fan speeds using AsusFanControl.exe."""
    try:
        result = subprocess.run(
            ["AsusFanControl.exe", "--get-fan-speeds"],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.strip()
        fan_speeds_str = output.split(": ")[1]  # Extract fan speeds string
        return fan_speeds_str
    except subprocess.CalledProcessError as e:
        logging.error(f"Error getting fan speeds: {e}")
        return None
    except IndexError:
        logging.error("Error parsing fan speeds output.")
        return None
    except FileNotFoundError:
        logging.error("AsusFanControl.exe not found. Ensure it is in the system PATH.")
        return None

current_set_fan_percentage = None  # Track the currently set fan percentage

def set_fan_speed(percentage):
    """Sets the fan speed using AsusFanControl.exe, only if necessary."""
    global current_set_fan_percentage
    if current_set_fan_percentage != percentage:  # Check if speed needs to be changed
        try:
            result = subprocess.run(
                ["AsusFanControl.exe", "--set-fan-speeds=" + str(percentage)],
                capture_output=True,
                text=True,
                check=True
            )
            output = result.stdout.strip()
            logging.info(output)  # Log confirmation message
            current_set_fan_percentage = percentage  # Update tracked percentage
        except subprocess.CalledProcessError as e:
            logging.error(f"Error setting fan speed: {e}")
        except FileNotFoundError:
            logging.error("AsusFanControl.exe not found. Ensure it is in the system PATH.")
    else:
        logging.info(f"Fan speed already set to {percentage}%, no need to set again.")

def decide_fan_speed(temp):
    """Decides the fan speed based on the CPU temperature."""
    if temp <= LOW_TEMP:
        return MIN_SPEED  # 30% for 20°C or below
    elif temp >= HIGH_TEMP:
        return MAX_SPEED  # 100% for 50°C or above
    else:
        # Linear interpolation between 30% at 20°C and 100% at 50°C
        return round(MIN_SPEED + (MAX_SPEED - MIN_SPEED) / (HIGH_TEMP - LOW_TEMP) * (temp - LOW_TEMP))

def adaptive_sleep(temp):
    """Returns a sleep time based on the current CPU temperature."""
    if temp is None or temp <= 0:
        return 3  # Short sleep if error
    elif temp < HIGH_TEMP - 10:  # < 40°C
        return 10  # Longer sleep
    elif temp < HIGH_TEMP:       # < 50°C
        return 5   # Medium sleep
    else:                        # >= 50°C
        return 3   # Shorter sleep

def main():
    while True:
        try:
            cpu_temp = get_cpu_temp()
            if cpu_temp is not None and cpu_temp > 0:
                fan_speed = decide_fan_speed(cpu_temp)
            else:
                fan_speed = 100  # Safety: 100% if temp is None or <= 0
            set_fan_speed(fan_speed)
            current_fan_speeds = get_current_fan_speeds()
            if current_fan_speeds:
                logging.info(f"CPU Temp: {cpu_temp if cpu_temp is not None else 'Unknown'} C, Fan Speed: {current_fan_speeds} (Set to {fan_speed}%)")
            else:
                logging.info(f"CPU Temp: {cpu_temp if cpu_temp is not None else 'Unknown'} C, Fan Speed: Unknown (Set to {fan_speed}%)")
            sleep_time = adaptive_sleep(cpu_temp)
            time.sleep(sleep_time)
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
        except KeyboardInterrupt:
            logging.info("Script terminated by user.")
            break

if __name__ == "__main__":
    main()