# ASUS Fan Control Enhanced 🌬️💻

A simple Python script to dynamically control your ASUS laptop's fan speeds based on CPU temperature, using the `AsusFanControl.exe` utility.

## ✨ Features

*   **Dynamic Fan Control:** Adjusts fan speed based on CPU temperature using a linear interpolation curve. 📈
*   **Temperature Thresholds:** Configurable low and high temperature thresholds for fan speed control. 🔥❄️
*   **Adaptive Sleep:** The script sleeps longer when the CPU is cool and shorter when it's hot, reducing unnecessary checks. 😴⏱️
*   **Logging:** Logs CPU temperature, fan speeds, and actions to a file (`fan_control.log`). 📄📊
*   **Error Handling:** Includes basic error handling for subprocess calls and parsing issues. ✅
*   **Efficient Updates:** Only attempts to set the fan speed if the required percentage has changed. 💪

## 🚨 Prerequisites

Before running this script, you need:

1.  **Python 3:** Make sure Python 3 is installed on your system.
2.  **`AsusFanControl.exe`:** This script relies heavily on the `AsusFanControl.exe` command-line utility. You need to download and place this executable in a location that is included in your system's PATH environment variable, or in the same directory as the script. You can usually find this utility bundled with fan control software for ASUS laptops or potentially in specific repositories dedicated to ASUS utilities.

## 📦 Installation

1.  **Clone or Download:** Get the `main.py` script. You can clone this repository if it's hosted here, or simply download the `main.py` file.
    ```bash
    git clone https://github.com/UtkarshPrajapati/ASUS-Fan-Control-using-ASUSFanControlEnhanced
    cd ASUS-Fan-Control-using-ASUSFanControlEnhanced
    ```
2.  **Install `AsusFanControl.exe`:** Ensure `AsusFanControl.exe` is accessible (either in the same directory or in your system PATH).

## ▶️ Usage

To run the script, simply execute the `main.py` file using Python:

```bash
python main.py
```

The script will run in a loop, periodically checking the CPU temperature and adjusting the fan speed.

To stop the script, press `Ctrl + C` in the terminal.

## ⚙️ Configuration

You can adjust the fan control behavior by modifying the following constants at the beginning of the `main.py` file:

*   `LOW_TEMP`: Temperature (°C) below which the fan speed is set to `MIN_SPEED`. (Default: `20`)
*   `HIGH_TEMP`: Temperature (°C) above which the fan speed is set to `MAX_SPEED`. (Default: `60`)
*   `MIN_SPEED`: Minimum fan speed percentage. (Default: `10`)
*   `MAX_SPEED`: Maximum fan speed percentage. (Default: `100`)

```python
# Define constants for temperature and speed
LOW_TEMP = 20    # Temperature where fan speed is at minimum
HIGH_TEMP = 60   # Temperature where fan speed reaches maximum
MIN_SPEED = 10   # Minimum fan speed percentage
MAX_SPEED = 100  # Maximum fan speed percentage
```

You can also adjust the logging file size and backup count:

*   `maxBytes`: Maximum size of the log file in bytes. (Default: `5000`)
*   `backupCount`: Number of backup log files to keep. (Default: `1`)

```python
handler = RotatingFileHandler('fan_control.log', maxBytes=5000, backupCount=1)
```

*Note: For better maintainability, consider moving these configuration options to a separate configuration file or using command-line arguments in the future.*

## 📜 Logging

The script logs its activity to a file named `fan_control.log` in the same directory as the script. The `fanlog.bat` file provides a simple way to view the last 10 lines of this log dynamically using the `%USERPROFILE%` environment variable to locate the file. The log file includes timestamps, log levels (INFO, ERROR), and messages indicating the CPU temperature, current fan speeds reported by the utility, and the fan speed percentage being set.

The log file uses a `RotatingFileHandler` to prevent it from growing too large, automatically creating backups when the size limit is reached.

## 🧠 How it Works

1.  **Get Temperature:** The script repeatedly calls `AsusFanControl.exe --get-cpu-temp` to fetch the current CPU temperature.
2.  **Decide Speed:** Based on the retrieved temperature, the `decide_fan_speed` function calculates the target fan speed percentage.
    *   If the temperature is at or below `LOW_TEMP`, the speed is set to `MIN_SPEED`.
    *   If the temperature is at or above `HIGH_TEMP`, the speed is set to `MAX_SPEED`.
    *   Between `LOW_TEMP` and `HIGH_TEMP`, the speed is linearly interpolated between `MIN_SPEED` and `MAX_SPEED`.
3.  **Set Speed:** The `set_fan_speed` function is called with the target percentage. It only executes the `AsusFanControl.exe --set-fan-speeds=<percentage>` command if the target percentage is different from the last one set, avoiding unnecessary command calls.
4.  **Log Information:** The script logs the current CPU temperature and the fan speeds reported by the utility.
5.  **Adaptive Sleep:** The `adaptive_sleep` function determines how long the script should wait before the next check. It sleeps for shorter durations when the CPU is hot and longer when it's cool.
6.  **Loop:** The process repeats indefinitely until the script is stopped.

## 👋 Contributing

Contributions are welcome! If you have suggestions for improvements or new features, feel free to open an issue or submit a pull request.