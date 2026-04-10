# ClickUp Calendar Sync

Automatically sync calendar events (sick leave, student leave, vacation, etc.) from iCal/Bizneo calendars to ClickUp tasks.

## Features

- Fetch events from iCal calendar URLs (Bizneo, Google Calendar, etc.)
- Automatically categorize events (sick leave, student leave, vacation)
- Create corresponding tasks in ClickUp with proper status and time estimates
- Add a specific recurring task daily for a fixed number of days
- Configurable event mappings and task templates
- Dry-run mode for testing
- Environment variable support for sensitive credentials

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your actual credentials:

```
CLICKUP_API_KEY=your_clickup_api_key_here
CLICKUP_LIST_ID=your_list_id_here
CLICKUP_TEAM_ID=your_team_id_here
BIZNEO_CALENDAR_URL=your_bizneo_calendar_url_here
```

### 3. Configure Sync Settings

Copy the example config file:

```bash
cp config.example.json config.json
```

Edit `config.json` to customize:
- Event type mappings (sick leave, student leave, vacation)
- Daily recurring task blocks (task name, start date, days, and hours)
- ClickUp status names
- Task naming conventions
- Time estimates

### 4. Get Your ClickUp Credentials

#### API Key
1. Go to ClickUp Settings > Apps
2. Click "Generate" under API Token
3. Copy your API key

#### List ID
1. Open the ClickUp list where you want tasks created
2. Look at the URL: `https://app.clickup.com/{team_id}/v/li/{list_id}`
3. Copy the list ID from the URL

#### Team ID
1. Go to ClickUp Settings
2. Look at the URL: `https://app.clickup.com/{team_id}/settings`
3. Copy the team ID from the URL

### 5. Get Your Calendar URL

#### For Bizneo:
- Get your Bizneo calendar iCal URL from your admin panel or calendar settings

#### For Google Calendar:
1. Open Google Calendar
2. Click the three dots next to your calendar
3. Go to Settings and sharing
4. Scroll to "Integrate calendar"
5. Copy the "Secret address in iCal format"

## Usage

### Basic Sync

Sync the next 30 days of calendar events:

```bash
python sync_calendar.py
```

### Sync Custom Time Range

Sync the next 60 days:

```bash
python sync_calendar.py --days 60
```

### Dry Run (Test Without Creating Tasks)

Test the sync without actually creating tasks:

```bash
python sync_calendar.py --dry-run
```

### Use Custom Config File

```bash
python sync_calendar.py --config my-config.json
```

## Daily Recurring Task (Fixed Number of Days)

If you want one specific existing task to be repeated every day for `x` days (or until a fixed end date), add it under `sync_settings.daily_recurring_tasks` in `config.json`:

```json
{
  "sync_settings": {
    "daily_recurring_tasks": [
      {
        "task_id": "",
        "custom_task_id": false,
        "task_name": "Daily Admin",
        "summary": "Daily Admin Block",
        "entry_description": "Planned recurring admin work",
        "start_date": "2026-04-10",
        "end_date": "2026-04-19",
        "days": 10,
        "hours": 2,
        "include_weekends": false
      }
    ]
  }
}
```

Notes:
- Use `task_id` (recommended) for an existing task, or `task_name` to find an existing task by name.
- This flow does not create new ClickUp tasks; it only adds time entries to existing tasks.
- If your `task_id` is a custom ID like `INF-1353`, set `custom_task_id: true` and ensure `CLICKUP_TEAM_ID` is set in `.env`.
- `start_date` format is `YYYY-MM-DD`. If omitted, it starts today.
- Use either `days` or `end_date`.
- `end_date` format is `YYYY-MM-DD` and is inclusive.
- If both `days` and `end_date` are provided, `end_date` takes precedence.
- `hours` defaults to `8` if omitted.
- `include_weekends` defaults to `false`.
- Existing entries on the same date are skipped automatically.

## Event Categorization

The script automatically categorizes events based on keywords in the summary or description:

- **Sick Leave**: Keywords like "sick", "illness", "doctor", "medical"
- **Student Leave**: Keywords like "student", "class", "course", "training"
- **Vacation**: Keywords like "vacation", "holiday", "pto", "time off"

You can customize these mappings in `config.json`.

## Automation

### Run Daily with Cron

Add to your crontab to sync automatically every day at 8 AM:

```bash
crontab -e
```

Add this line:

```
0 8 * * * cd /home/sacramento_mgb/clickup-calendar-sync && /usr/bin/python3 sync_calendar.py
```

### Run with Systemd Timer

Create a systemd service file at `/etc/systemd/system/clickup-sync.service`:

```ini
[Unit]
Description=ClickUp Calendar Sync
After=network.target

[Service]
Type=oneshot
User=sacramento_mgb
WorkingDirectory=/home/sacramento_mgb/clickup-calendar-sync
ExecStart=/usr/bin/python3 /home/sacramento_mgb/clickup-calendar-sync/sync_calendar.py
```

Create a timer file at `/etc/systemd/system/clickup-sync.timer`:

```ini
[Unit]
Description=Run ClickUp Calendar Sync daily

[Timer]
OnCalendar=daily
OnCalendar=08:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
sudo systemctl enable clickup-sync.timer
sudo systemctl start clickup-sync.timer
```

## Troubleshooting

### Authentication Errors

- Verify your ClickUp API key is correct
- Make sure your API key has permission to create tasks in the specified list

### No Events Found

- Check that your calendar URL is accessible
- Verify the iCal URL is correct (should end in `.ics` or return iCal format)
- Try accessing the URL in a browser to test

### Tasks Not Created

- Run with `--dry-run` to see what would be created
- Check ClickUp list permissions
- Verify the status names in config.json match your ClickUp list statuses

### Calendar Parse Errors

- Make sure the calendar is in iCal format
- Some calendar providers may require authentication (not yet supported)

## Configuration Reference

### config.json Structure

```json
{
  "clickup": {
    "api_key": "Your API key (or use .env)",
    "list_id": "Target list ID",
    "team_id": "Your team/workspace ID"
  },
  "bizneo": {
    "calendar_url": "iCal URL",
    "calendar_type": "ical"
  },
  "sync_settings": {
    "event_mappings": {
      "vacation": {
        "clickup_task_name": "Vacations",
        "description": "Vacation day"
      },
      "student_leave": {
        "clickup_task_name": "Student worker protocol",
        "description": "Student leave"
      },
      "sick_leave": {
        "clickup_task_name": "Sick leave",
        "description": "Sick leave"
      }
    },
    "daily_recurring_tasks": [
      {
        "task_id": "",
        "task_name": "Daily Admin",
        "summary": "Daily Admin Block",
        "entry_description": "Planned recurring admin work",
        "start_date": "2026-04-10",
        "end_date": "2026-04-14",
        "days": 5,
        "hours": 2,
        "include_weekends": false
      }
    ]
  }
}
```

## License

MIT
