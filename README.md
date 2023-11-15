# ICS Clip

## Description
ICS Clip is a Python script designed to streamline calendar files (.ics) by removing events prior to the current week while preserving current, future and recurring events from the file. This can significantly reduce the size of large calendar files, making them more manageable and easier to import into calendar applications like Google Calendar.

## Installation
To run ICS Clip, you'll need Python installed on your system along with a few external libraries. You can install the required libraries using pip:

```bash
pip install icalendar pytz python-dateutil
```
## Usage
To use ICS Clip, place your .ics file in the same directory as the script or provide the path to the file. Run the script with Python:

```bash
python icsclip.py
```

The script will create a new .ics file with the filtered events.

## ToDo
Add user parameters to define cuttoff date.

## Contributing
Contributions to ICS Clip are welcome! If you have suggestions for improvements or bug fixes, please open an issue or a pull request.

## License
[MIT](https://choosealicense.com/licenses/mit/)





