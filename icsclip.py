#!/usr/bin/env python3
import argparse
import os
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
import re

from icalendar import Calendar, Event, vDatetime, vText

LOCAL_TZ = ZoneInfo('UTC')  # replaced in main() from --timezone


def system_timezone():
    """Name of this machine's IANA timezone, or 'UTC' if it can't be determined."""
    name = os.environ.get('TZ', '').lstrip(':')
    if not name:
        try:
            name = os.path.realpath('/etc/localtime').split('zoneinfo/', 1)[1]
        except (OSError, IndexError):
            name = ''
    try:
        ZoneInfo(name)
        return name
    except Exception:
        return 'UTC'

# Only these event properties survive; everything else (description, location, URLs,
# Teams/X-MICROSOFT data, ...) is dropped. ATTENDEE/ORGANIZER are handled separately.
KEEP_PROPS = {
    'UID', 'DTSTAMP', 'DTSTART', 'DTEND', 'DURATION', 'RRULE', 'RDATE', 'EXDATE',
    'RECURRENCE-ID', 'SEQUENCE', 'STATUS', 'TRANSP', 'CLASS', 'SUMMARY',
}


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def norm_email(value):
    return str(value).lower().removeprefix('mailto:').strip()


def to_datetime(value):
    """Convert a date/datetime (or icalendar wrapper) to an aware datetime in LOCAL_TZ."""
    value = getattr(value, 'dt', value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=LOCAL_TZ) if value.tzinfo is None else value.astimezone(LOCAL_TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=LOCAL_TZ)
    return None


def event_times(ev):
    """Return (start, end) in LOCAL_TZ; end is inferred when the event has no DTEND."""
    dtstart = ev.get('DTSTART')
    start = to_datetime(dtstart) if dtstart else None
    if start is None:
        return None, None
    if ev.get('DTEND'):
        return start, to_datetime(ev['DTEND'])
    if ev.get('DURATION'):
        return start, start + ev['DURATION'].dt
    all_day = isinstance(dtstart.dt, date) and not isinstance(dtstart.dt, datetime)
    return start, start + (timedelta(days=1) if all_day else timedelta())


def is_skipped(ev, skips):
    uid = str(ev.get('UID', '')).lower()
    return any(text.lower() in uid for text in skips)


def make_is_me(my_emails):
    def is_me(attendee):
        emails = {norm_email(attendee)}
        extra = (getattr(attendee, 'params', None) or {}).get('EMAIL')
        if extra:
            emails.add(norm_email(extra))
        return bool(emails & my_emails)
    return is_me


def is_declined(ev, is_me):
    summary = str(ev.get('SUMMARY') or '').strip().lower()
    if summary.startswith('declined:') or str(ev.get('STATUS') or '').upper() == 'CANCELLED':
        return True
    for attendee in as_list(ev.get('ATTENDEE')):
        partstat = (getattr(attendee, 'params', None) or {}).get('PARTSTAT', '')
        if is_me(attendee) and str(partstat).upper() == 'DECLINED':
            return True
    return False


def recurrence_has_future(ev, cutoff):
    """True if a recurring event has an occurrence ending on/after cutoff (or can't be judged)."""
    start, end = event_times(ev)
    if start is None:
        return True
    duration = (end - start) if end else timedelta()

    for rdate in as_list(ev.get('RDATE')):
        for d in as_list(getattr(rdate, 'dts', [])):
            moment = to_datetime(d.dt if hasattr(d, 'dt') else d)
            if moment and moment + duration >= cutoff:
                return True

    if 'RRULE' not in ev:
        return False
    try:
        rule = rrulestr(as_list(ev['RRULE'])[0].to_ical().decode('utf-8'), dtstart=start)
        return rule.after(cutoff - duration, inc=True) is not None
    except (ValueError, TypeError):
        return True  # can't evaluate the rule; keep rather than risk losing the series


SEQUENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def revision_bump():
    """Minutes since SEQUENCE_EPOCH. Added to every SEQUENCE so each run counts as a newer
    revision; Google Calendar ignores an imported event whose SEQUENCE isn't higher than the
    copy it already has, which would leave previously imported full-detail events in place."""
    return int((datetime.now(timezone.utc) - SEQUENCE_EPOCH).total_seconds() // 60)


def series_key(ev):
    """Identify 'the same meeting' across re-issued series: title, weekday and local start time."""
    start = event_times(ev)[0]
    if start is None or 'RRULE' not in ev:
        return None
    title = ' '.join(str(ev.get('SUMMARY') or '').lower().split())
    return (title, start.weekday(), start.hour, start.minute)


def norm_title(ev):
    return ' '.join(str(ev.get('SUMMARY') or '').lower().split())


def series_occurs_at(series, when):
    """True if the recurring event has an (un-skipped) occurrence starting exactly at `when`."""
    start = event_times(series)[0]
    if start is None or 'RRULE' not in series:
        return False
    skipped = {to_datetime(d.dt if hasattr(d, 'dt') else d)
               for x in as_list(series.get('EXDATE')) for d in getattr(x, 'dts', [])}
    try:
        rule = rrulestr(as_list(series['RRULE'])[0].to_ical().decode('utf-8'), dtstart=start)
        return bool(rule.between(when, when, inc=True)) and when not in skipped
    except (ValueError, TypeError):
        return False


MOVE_WINDOW = timedelta(days=3)
MIN_SERIES_GAP = timedelta(days=20)


def moved_occurrence(series, single_start, overridden):
    """If a single event looks like one occurrence of an infrequent (monthly-ish) series moved by
    a few days, return the occurrence it replaces. Weekly-ish series are skipped: a nearby event
    with the same title is more likely an extra session than a moved one."""
    start = event_times(series)[0]
    if start is None or 'RRULE' not in series:
        return None
    skipped = {to_datetime(d.dt if hasattr(d, 'dt') else d)
               for x in as_list(series.get('EXDATE')) for d in getattr(x, 'dts', [])}
    try:
        rule = rrulestr(as_list(series['RRULE'])[0].to_ical().decode('utf-8'), dtstart=start)
        near = [o for o in rule.between(single_start - MOVE_WINDOW, single_start + MOVE_WINDOW, inc=True)
                if o not in skipped and o not in overridden and o != single_start]
        if not near:
            return None
        occurrence = min(near, key=lambda o: abs(o - single_start))
        before, after = rule.before(occurrence), rule.after(occurrence)
    except (ValueError, TypeError):
        return None
    if (before and occurrence - before < MIN_SERIES_GAP) or (after and after - occurrence < MIN_SERIES_GAP):
        return None
    return occurrence


def truncate_series(ev, at):
    """End a recurring series just before `at` (an aware datetime)."""
    rule = as_list(ev['RRULE'])[0]
    rule.pop('COUNT', None)
    rule['UNTIL'] = [(at - timedelta(seconds=1)).astimezone(timezone.utc)]


# UIDs produced by Outlook (04000000...) or Apple/iCal (GUID) exports. Anything else in the
# Google calendar (UIDs ending @google.com, other tools) was added some other way: left alone.
EXPORTED_UID = re.compile(r'^(04000000[0-9A-F]+|[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12})$', re.I)


def utc_value(prop):
    """A DTSTART/DTEND property as an all-day date or a UTC datetime."""
    value = prop.dt
    return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


def cancellations(google_path, live_events, bump):
    """STATUS:CANCELLED copies of events in a Google Calendar export that this run no longer
    sends. Importing them deletes the old copies. Events that did not come from an Outlook/Apple
    export are only cancelled when they duplicate an event this run keeps (same title and
    weekday/time, or same title and start). Returns (events, left_alone, native_duplicates)."""
    with open(google_path, 'rb') as f:
        existing = Calendar.from_ical(f.read()).walk('VEVENT')
    kept_uids = {str(e['UID']) for e in live_events}
    live_masters = [e for e in live_events if 'RECURRENCE-ID' not in e]
    live_series = [e for e in live_masters if 'RRULE' in e]
    live_series_keys = {series_key(e) for e in live_series}
    live_singles = {(norm_title(e), event_times(e)[0]) for e in live_masters if 'RRULE' not in e}

    def duplicates_live_event(e):
        start = event_times(e)[0]
        if 'RRULE' in e:
            return series_key(e) in live_series_keys
        return (norm_title(e), start) in live_singles or any(
            norm_title(s) == norm_title(e) and series_occurs_at(s, start) for s in live_series)

    master_uids = {str(e['UID']) for e in existing if 'RECURRENCE-ID' not in e}
    out, left_alone, native_duplicates = [], [], []
    for e in existing:
        uid = str(e['UID'])
        if uid in kept_uids:
            continue
        if not EXPORTED_UID.match(uid):
            if 'RECURRENCE-ID' in e:
                continue
            label = f"{str(e.get('SUMMARY'))[:60]} ({event_times(e)[0].date()})"
            if not duplicates_live_event(e):
                left_alone.append(label)
                continue
            native_duplicates.append(label)
        if 'RECURRENCE-ID' in e and uid in master_uids:
            continue  # cancelling the series removes its edited instances
        t = Event()
        t['UID'] = uid
        t['SUMMARY'] = vText('Cancelled')
        t['DTSTAMP'] = vDatetime(datetime.now(timezone.utc))
        t['SEQUENCE'] = int(e.get('SEQUENCE') or 0) + bump
        t['STATUS'] = 'CANCELLED'
        t.add('DTSTART', utc_value(e['DTSTART']))
        if e.get('DTEND'):
            t.add('DTEND', utc_value(e['DTEND']))
        if 'RECURRENCE-ID' in e:
            t.add('RECURRENCE-ID', utc_value(e['RECURRENCE-ID']))
        elif 'RRULE' in e:
            t['RRULE'] = e['RRULE']
        out.append(t)
    return out, left_alone, native_duplicates


def scrub_event(ev, is_me, title_mode, stats, bump=0):
    for key in list(ev.keys()):
        upper = key.upper()
        if upper in KEEP_PROPS:
            continue
        if upper in ('ATTENDEE', 'ORGANIZER'):
            values = as_list(ev[key])
            mine = [v for v in values if is_me(v)]
            if upper == 'ATTENDEE':
                stats['attendees'] += len(values) - len(mine)
            if mine:
                ev[key] = mine if len(mine) > 1 else mine[0]
                continue
        else:
            stats['fields'] += 1
        del ev[key]

    stats['alarms'] += len(ev.subcomponents)
    ev.subcomponents.clear()  # VALARM reminders
    ev['DTSTAMP'] = vDatetime(datetime.now(timezone.utc))
    # Google keeps fields an import leaves out, so send them blank to overwrite any copy it holds
    ev['DESCRIPTION'] = vText('')
    ev['LOCATION'] = vText('')
    ev['SEQUENCE'] = int(ev.get('SEQUENCE') or 0) + bump
    if title_mode == 'busy':
        ev['SUMMARY'] = vText('Busy')


def main():
    parser = argparse.ArgumentParser(
        description='ICS Clip: trim an .ics to this week onward and strip it down for sharing.')
    parser.add_argument('calendar_file', help='Path to the .ics calendar file')
    parser.add_argument('--me', action='append', default=[], metavar='EMAIL',
                        help='Your email address (repeatable). You are kept as the only '
                             'attendee/organizer, and used to detect meetings you declined. '
                             'Without it, all attendees and organizers are removed.')
    parser.add_argument('--title', choices=['keep', 'busy'], default='keep',
                        help="'keep' leaves event titles alone; 'busy' replaces every title with 'Busy'")
    parser.add_argument('--skip', action='append', default=[], metavar='TEXT',
                        help='Drop events (and their edited instances) whose UID contains TEXT, '
                             'case-insensitively; the summary prints the UID of every event it skips. '
                             'Repeatable. Handy for read-only events you cannot decline in your '
                             'calendar app.')
    parser.add_argument('--timezone', metavar='ZONE', default=system_timezone(),
                        help='IANA timezone (e.g. America/Chicago) used for the week cutoff and for '
                             'events without a timezone. Default: this computer\'s timezone '
                             '(%(default)s).')
    parser.add_argument('--since', metavar='YYYY-MM-DD', type=date.fromisoformat,
                        help='Keep events from this date on, instead of from the start of the current '
                             'week. Use an early date once to re-send (and so scrub) past events that '
                             'were imported to Google Calendar before.')
    parser.add_argument('--cancel-missing', metavar='GOOGLE_EXPORT.ics',
                        help="A Google Calendar export (.ics) of the calendar you import into. Events "
                             "in it that came from an earlier Outlook/Apple export but are not in this "
                             "run's output (past events, replaced duplicates) are added as cancellations, "
                             "so importing the result deletes them from Google Calendar.")
    parser.add_argument('--cancel-chunk', metavar='N', type=int, default=250,
                        help='With --cancel-missing: cancellations go into separate files of at most N '
                             'events each (default 250); Google Calendar silently skips many events '
                             'when too many are imported at once.')
    args = parser.parse_args()
    global LOCAL_TZ
    try:
        LOCAL_TZ = ZoneInfo(args.timezone)
    except Exception:
        parser.error(f'unknown timezone: {args.timezone}')

    is_me = make_is_me({norm_email(a) for a in args.me})

    with open(args.calendar_file, 'rb') as f:
        cal = Calendar.from_ical(f.read())
    cal['VERSION'] = '2.0'
    cal['METHOD'] = 'PUBLISH'

    # Cutoff: start of the current week (Monday 00:00, local time) unless --since is given
    now = datetime.now(LOCAL_TZ)
    if args.since:
        start_of_week = datetime(args.since.year, args.since.month, args.since.day, tzinfo=LOCAL_TZ)
    else:
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    bump = revision_bump()

    removed = {'declined': 0, 'old_single': 0, 'expired_recurring': 0, 'superseded_instances': 0, 'duplicate_single': 0, 'identical_single': 0, 'skipped': 0, 'other': 0}
    stats = {'attendees': 0, 'fields': 0, 'alarms': 0}
    truncated = recurring_kept = 0
    truncated_notes = []

    vevents = []
    for component in list(cal.subcomponents):
        if component.name == 'VEVENT':
            vevents.append(component)
        elif component.name != 'VTIMEZONE':
            cal.subcomponents.remove(component)
            removed['other'] += 1
    events_in = len(vevents)

    def drop(ev, reason):
        cal.subcomponents.remove(ev)
        removed[reason] += 1

    masters = [e for e in vevents if 'RECURRENCE-ID' not in e]
    overrides = [e for e in vevents if 'RECURRENCE-ID' in e]

    # 1) Declined/cancelled meetings and anything excluded with --skip
    skipped_notes = []
    for ev in list(masters):
        if is_skipped(ev, args.skip):
            masters.remove(ev)
            drop(ev, 'skipped')
            skipped_notes.append(f"{str(ev.get('SUMMARY'))[:60]!r} ({str(ev['UID'])[:36]})")
        elif is_declined(ev, is_me):
            masters.remove(ev)
            drop(ev, 'declined')

    # 2) A series re-issued by the organizer leaves the old one running; end the old one
    #    where the newer same-title/weekday/time series begins
    cut_at = {}  # UID -> when its series was cut short
    groups = {}
    for ev in masters:
        key = series_key(ev)
        if key:
            groups.setdefault(key, []).append(ev)
    for group in groups.values():
        group.sort(key=lambda e: event_times(e)[0])
        for older, newer in zip(group, group[1:]):
            newer_start = event_times(newer)[0]
            if event_times(older)[0] < newer_start and recurrence_has_future(older, newer_start):
                truncate_series(older, newer_start)
                cut_at[str(older['UID'])] = min(cut_at.get(str(older['UID']), newer_start), newer_start)
                truncated += 1
                truncated_notes.append(f"{str(older.get('SUMMARY'))[:60]!r}: series from "
                                       f"{event_times(older)[0].date()} ended where the one from "
                                       f"{newer_start.date()} begins")

    # 3) Expired recurring series, old single events, and single events that merely repeat
    #    an occurrence of a series being kept
    live_series = {}
    for ev in masters:
        if 'RRULE' in ev and recurrence_has_future(ev, start_of_week):
            live_series.setdefault(norm_title(ev), []).append(ev)
    kept_series = set()
    kept_series_events, kept_singles = [], []
    seen_singles = set()
    for ev in masters:
        recurring = 'RRULE' in ev or 'RDATE' in ev
        end = event_times(ev)[1]
        if recurring and not recurrence_has_future(ev, start_of_week):
            drop(ev, 'expired_recurring')
        elif not recurring and end is not None and end < start_of_week:
            drop(ev, 'old_single')
        elif not recurring and any(series_occurs_at(series, event_times(ev)[0])
                                   for series in live_series.get(norm_title(ev), [])):
            drop(ev, 'duplicate_single')
        elif not recurring and (norm_title(ev), *event_times(ev)) in seen_singles:
            drop(ev, 'identical_single')
        else:
            if recurring:
                kept_series.add(str(ev['UID']))
                recurring_kept += 1
                kept_series_events.append(ev)
            else:
                kept_singles.append(ev)
                seen_singles.add((norm_title(ev), *event_times(ev)))
            scrub_event(ev, is_me, args.title, stats, bump)

    # 4) Edited instances of a series: keep (even past ones, so a copy already in Google gets
    #    overwritten) whenever the series itself is kept
    for ev in overrides:
        uid = str(ev['UID'])
        if is_skipped(ev, args.skip):
            drop(ev, 'skipped')
        elif is_declined(ev, is_me):
            drop(ev, 'declined')
        elif uid in kept_series:
            if uid in cut_at and to_datetime(ev['RECURRENCE-ID']) >= cut_at[uid]:
                drop(ev, 'superseded_instances')
            else:
                scrub_event(ev, is_me, args.title, stats, bump)
        else:
            end = event_times(ev)[1]
            if end is not None and end < start_of_week:
                drop(ev, 'old_single')
            else:
                scrub_event(ev, is_me, args.title, stats, bump)

    # 5) The organizer moved one occurrence of an infrequent series by sending a separate single
    #    event, but the original occurrence is still in the export: skip the original
    moved_notes = []
    for series in kept_series_events:
        uid = str(series['UID'])
        overridden = {to_datetime(o['RECURRENCE-ID']) for o in overrides
                      if str(o['UID']) == uid and o in cal.subcomponents}
        for single in kept_singles:
            if norm_title(single) != norm_title(series):
                continue
            occurrence = moved_occurrence(series, event_times(single)[0], overridden)
            if occurrence:
                series.add('EXDATE', occurrence.astimezone(timezone.utc))
                moved_notes.append(f"{str(series.get('SUMMARY'))[:60]!r}: skipped {occurrence.date()}, "
                                   f"moved to {event_times(single)[0].date()}")

    events_out = events_in - sum(removed.values()) + removed['other']

    cancelled, left_alone, native_duplicates = [], [], []
    if args.cancel_missing:
        live_events = [c for c in cal.subcomponents if c.name == 'VEVENT']
        cancelled, left_alone, native_duplicates = cancellations(args.cancel_missing, live_events, bump)

    dir_name, file_name = os.path.split(args.calendar_file)
    new_calendar_file = os.path.join(dir_name, 'updated_' + file_name)
    with open(new_calendar_file, 'wb') as f:
        f.write(cal.to_ical())

    cancel_files = []
    base, ext = os.path.splitext(new_calendar_file)
    for n, start in enumerate(range(0, len(cancelled), args.cancel_chunk), 1):
        chunk = Calendar()
        chunk['PRODID'] = '-//icsclip cancellations//EN'
        chunk['VERSION'] = '2.0'
        chunk['METHOD'] = 'PUBLISH'
        for tombstone in cancelled[start:start + args.cancel_chunk]:
            chunk.add_component(tombstone)
        path = f'{base}_cancel_{n:02d}{ext}'
        with open(path, 'wb') as f:
            f.write(chunk.to_ical())
        cancel_files.append(path)

    print(f"Summary: {events_in} event(s) in, {events_out} out")
    print(f"  - Removed {removed['declined']} declined/cancelled event(s).")
    if args.skip:
        print(f"  - Skipped {removed['skipped']} event(s) matching --skip:")
        for note in skipped_notes:
            print(f"      * {note}")
    print(f"  - Removed {removed['old_single']} old single event(s).")
    print(f"  - Removed {removed['duplicate_single']} single event(s) that repeat an occurrence of a kept series "
          f"and {removed['identical_single']} identical copy(ies) of another single event.")
    print(f"  - Removed {removed['expired_recurring']} expired recurring series "
          f"(kept {recurring_kept} recurring series intact).")
    print(f"  - Ended {truncated} series early because a newer copy of the same meeting replaced it "
          f"({removed['superseded_instances']} stale edited instance(s) dropped).")
    for note in truncated_notes:
        print(f"      * {note}")
    if moved_notes:
        print(f"  - Skipped {len(moved_notes)} occurrence(s) of a series because the organizer moved it "
              f"to a separate event:")
        for note in moved_notes:
            print(f"      * {note}")
    print(f"  - Stripped {stats['attendees']} other attendee(s), {stats['fields']} field(s) "
          f"and {stats['alarms']} reminder(s).")
    if args.cancel_missing:
        print(f"  - Found {len(cancelled)} event(s) already in Google Calendar that are no longer in "
              f"this export; cancellations written to {len(cancel_files)} file(s):")
        for path in cancel_files:
            print(f"      * {path}")
        if native_duplicates:
            print(f"  - Including {len(native_duplicates)} Google-created duplicate(s) of events kept above:")
            for title in native_duplicates:
                print(f"      * {title}")
        if left_alone:
            print(f"  - Left alone {len(left_alone)} event(s) in Google Calendar that did not come from an "
                  f"Outlook/Apple export (review these by hand):")
            for title in left_alone:
                print(f"      * {title}")
    print(f"Updated calendar saved to: {new_calendar_file}")


if __name__ == '__main__':
    main()
