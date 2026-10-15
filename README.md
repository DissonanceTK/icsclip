# ICS Clip

ICS Clip shrinks a large calendar export (`.ics`) and scrubs it for sharing. The typical use is importing a work calendar into a family or shared Google Calendar so others can see when you are busy, without the meeting details, attendee lists or customer names.

It reads an export from Outlook or Apple Calendar and writes `updated_<name>.ics` next to it.

## What it does

**Trims**
- Removes events that ended before the current week (Monday 00:00), or before `--since`.
- Removes recurring series that have ended. Active series are kept whole, with their original start, so Google Calendar does not split them into separate events.
- Removes meetings you declined (`Declined:` titles, your own `PARTSTAT=DECLINED`) and cancelled ones.
- Removes duplicates: a single event that repeats an occurrence of a kept series, identical copies of the same single event, and an older recurring series that a newer one for the same meeting (same title, weekday and time) has replaced.
- Skips an occurrence of a monthly-ish series when the organizer moved it to a separate single event a few days away.

**Scrubs**
- Keeps only times, recurrence, status and the title. Descriptions, locations, URLs, Teams/Webex links, reminders, attachments and vendor-specific fields are removed.
- Removes every attendee and organizer except you (see `--me`).
- With `--title busy`, replaces every title with "Busy".

**Makes re-importing work.** Google Calendar ignores an imported event whose `SEQUENCE` is not higher than the copy it already has, and it keeps fields an import leaves out. So each run raises every `SEQUENCE` and sends the description and location blank, which lets a re-import overwrite what an earlier import left behind.

## Install

Requires Python 3.9 or newer.

```bash
git clone <this repository>
cd icsclip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The dependencies are `icalendar` and `python-dateutil`. Use the venv's Python to run the script (`.venv/bin/python icsclip.py ...`). You do not need to activate the venv.

## Usage

1. Export your calendar to an `.ics` file (for example, File > Export in Apple Calendar, or save your calendar as an iCalendar file from Outlook).
2. Run:

   ```bash
   .venv/bin/python icsclip.py --me you@example.com Calendar.ics
   ```

3. Check the summary it prints, then import `updated_Calendar.ics`.

Add `--title busy` if event titles could contain names you do not want to share.

### Options

| Option | Meaning |
|---|---|
| `--me EMAIL` | Your email address; repeat it for several. You stay on your events as the only attendee, and the script uses it to spot meetings you declined. Without it, all attendees and organizers are removed. |
| `--title {keep,busy}` | `keep` (default) leaves titles alone. `busy` replaces them all with "Busy". |
| `--timezone ZONE` | IANA timezone such as `America/Chicago`. Used for the week cutoff and for events that have no timezone. Defaults to this computer's timezone. |
| `--since YYYY-MM-DD` | Keep events from this date instead of from the start of this week. |
| `--skip TEXT` | Drop events whose UID contains `TEXT` (case-insensitive), along with their edited instances. Repeatable. The summary prints the UID of each skipped event. Use it for events you cannot decline in your calendar app, such as read-only ones. |
| `--cancel-missing FILE` | A Google Calendar export of the calendar you import into. See [Cleaning up a Google Calendar](#cleaning-up-a-google-calendar). |
| `--cancel-chunk N` | With `--cancel-missing`, the maximum number of cancellations per file. Default 250. |

### A shell alias

Once the options are settled, an alias makes it one command. Replace the paths and address with yours:

```bash
alias clipics='/path/to/icsclip/.venv/bin/python /path/to/icsclip/icsclip.py ~/Downloads/Calendar.ics --me you@example.com'
```

## Importing into Google Calendar

1. In Google Calendar, open Settings > Import & export, choose the `updated_*.ics` file, and pick the calendar to import into.
2. Import into a calendar you create for this purpose, so nothing else in it can be affected.
3. Running the script again later and importing the new file updates existing events. They match by UID, and the raised `SEQUENCE` makes Google accept the change.

Google imports can only add and update events. They cannot remove events that are no longer in the file, which is what `--cancel-missing` is for.

## Cleaning up a Google Calendar

Use this when the calendar already holds events from earlier imports: past events that should go, duplicates caused by a different export format (the same meeting under a different UID), or full-detail copies you want scrubbed.

1. Export the target calendar from Google (Settings > Import & export > Export) and unzip it.
2. Run the script with `--cancel-missing`:

   ```bash
   .venv/bin/python icsclip.py --me you@example.com Calendar.ics --cancel-missing /path/to/google-export.ics
   ```

3. Import `updated_Calendar.ics` as usual.
4. Import each `updated_Calendar_cancel_NN.ics` file, one at a time. These contain `STATUS:CANCELLED` copies of events that are in Google but no longer in this run's output, and importing them deletes those events.
5. Export the Google calendar again and re-run. Whatever is still left is cancelled on the second pass.

Notes:
- Only events that came from an Outlook or Apple export (Outlook `04000000...` or GUID-style UIDs) are cancelled automatically. Events added some other way, such as UIDs ending in `@google.com`, are left alone and listed in the summary, unless they duplicate an event this run keeps.
- Cancellations are split into files of 250 because Google Calendar silently skips many events when too many are imported at once. After importing, export and re-run to see whether anything remains.
- Cancelling deletes events, including past ones, from the shared calendar. Keep your Google export as a backup.

## Things to know

- **Titles are not scrubbed by default.** They can contain customer or project names. Use `--title busy`, or review the output file first.
- **Webex or Teams links may remain on events imported before.** Google builds a "conference" field from links in a description, and an import cannot clear it. New imports do not have it.
- **An import that reports "0 out of 0" means the file could not be parsed.** Re-run the script with the current version and make sure you are importing the `updated_*` file.
- **A read-only event you cannot decline** still shows up if your calendar app contains it. Use `--skip` with its UID.
- **Heuristics.** Series replacement and moved-occurrence detection are based on title, time and organizer patterns. The summary lists every series it ended early and every occurrence it skipped, so you can check them.
- The script assumes standard Outlook and Apple Calendar exports. Other sources have not been tested.

## Contributing

Contributions are welcome. Open an issue or a pull request.

## License

[MIT](https://choosealicense.com/licenses/mit/)
