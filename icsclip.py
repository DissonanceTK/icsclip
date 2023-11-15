from icalendar import Calendar
from datetime import datetime, timedelta, timezone, date
from dateutil.rrule import rrulestr
from dateutil.relativedelta import relativedelta
import pytz

# Load the .ics file
with open('Calendar.ics', 'rb') as file:
    cal = Calendar.from_ical(file.read())

# Get the current date and the start of the current week in UTC
now = datetime.now(pytz.utc)
start_of_week = now - timedelta(days=now.weekday())

# Function to check if a recurring event occurs during or after the current week
def is_recurring_event_occuring(event, start_of_week):
    if 'RRULE' in event:
        dtstart = event['DTSTART'].dt
        # Make sure dtstart is timezone-aware
        if isinstance(dtstart, datetime):
            if dtstart.tzinfo is None:
                # Assume the local timezone for a naive datetime
                dtstart = pytz.timezone('America/Chicago').localize(dtstart)
            else:
                dtstart = dtstart.astimezone(pytz.utc)
        else:
            # If dtstart is a date, assume it starts at midnight of that date in the local timezone
            dtstart = datetime.combine(dtstart, datetime.min.time()).replace(tzinfo=pytz.timezone('America/Chicago'))

        rrule = rrulestr(event['RRULE'].to_ical().decode('utf-8'), dtstart=dtstart)
        # Check the next occurrence of this event
        next_occurrence = rrule.after(start_of_week - relativedelta(weeks=1))
        return next_occurrence is not None and next_occurrence >= start_of_week
    return False

# Filter out past events, considering their timezone and recurrence
events_to_remove = []
for component in cal.walk():
    if component.name == "VEVENT":
        dtstart = component.get('dtstart').dt
        # Handle datetime and date types for dtstart
        if isinstance(dtstart, datetime):
            if dtstart.tzinfo is None:
                # Assume the local timezone for a naive datetime
                dtstart = pytz.timezone('America/Chicago').localize(dtstart)
            else:
                dtstart = dtstart.astimezone(pytz.utc)
        elif isinstance(dtstart, date):
            # If dtstart is a date, assume it starts at midnight of that date in the local timezone
            dtstart = datetime.combine(dtstart, datetime.min.time()).replace(tzinfo=pytz.timezone('America/Chicago'))

        # Check if the event is a recurring event and occurs in the current week or in the future
        if is_recurring_event_occuring(component, start_of_week):
            continue
        if dtstart < start_of_week:
            events_to_remove.append(component)

# Remove the events that are in the past and not recurring
for event in events_to_remove:
    cal.subcomponents.remove(event)

# Save the updated calendar to a new .ics file
with open('updated_calendar.ics', 'wb') as file:
    file.write(cal.to_ical())
