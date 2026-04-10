#!/usr/bin/env python3
"""
ClickUp Calendar Sync Script
Syncs calendar events (sick leave, student leave, vacation, etc.) to ClickUp tasks
"""

import os
import json
import requests
from datetime import datetime, timedelta
from icalendar import Calendar
import pytz
from dotenv import load_dotenv
from typing import Dict, List, Optional

# Load environment variables
load_dotenv()


class ClickUpCalendarSync:
    def __init__(self, config_path: str = "config.json"):
        """Initialize the sync client with configuration"""
        # Load credentials from environment variables
        self.clickup_api_key = os.getenv('CLICKUP_API_KEY')
        self.clickup_list_id = os.getenv('CLICKUP_LIST_ID')
        self.clickup_team_id = os.getenv('CLICKUP_TEAM_ID')
        self.calendar_url = os.getenv('BIZNEO_CALENDAR_URL')
        self.public_holidays_url = os.getenv('PUBLIC_HOLIDAYS_CALENDAR_URL')

        # Load config file for event mappings
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"Config file '{config_path}' not found.\n"
                f"Please copy config.example.json to config.json and customize it."
            )

        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.sync_settings = self.config.get('sync_settings', {})
        self.event_mappings = self.sync_settings.get('event_mappings', {})
        self.daily_recurring_tasks = self.sync_settings.get('daily_recurring_tasks', [])

        # Validate minimal required environment variables.
        if not self.clickup_api_key:
            raise ValueError(
                "Missing required environment variable. Please set:\n"
                "  CLICKUP_API_KEY\n"
                "Check your .env file."
            )

        if not self.event_mappings and not self.daily_recurring_tasks:
            raise ValueError(
                "No sync rules found in config file.\n"
                "Please configure at least one of:\n"
                "  sync_settings.event_mappings\n"
                "  sync_settings.daily_recurring_tasks"
            )

        self.headers = {
            'Authorization': self.clickup_api_key,
            'Content-Type': 'application/json'
        }

    def fetch_calendar_from_url(self, url: str, calendar_name: str = "calendar") -> List[Dict]:
        """Fetch and parse events from a single iCal URL"""
        print(f"Fetching {calendar_name} from {url}...")

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Parse iCal data
            calendar = Calendar.from_ical(response.content)
            events = []

            # Get current date range
            now = datetime.now(pytz.UTC)

            for component in calendar.walk():
                if component.name == "VEVENT":
                    event_start = component.get('dtstart').dt
                    event_end = component.get('dtend').dt

                    # Convert to datetime if date only
                    if isinstance(event_start, datetime):
                        if event_start.tzinfo is None:
                            event_start = pytz.UTC.localize(event_start)
                    else:
                        event_start = datetime.combine(event_start, datetime.min.time())
                        event_start = pytz.UTC.localize(event_start)

                    if isinstance(event_end, datetime):
                        if event_end.tzinfo is None:
                            event_end = pytz.UTC.localize(event_end)
                    else:
                        # For all-day events, iCal uses exclusive end dates
                        # So we subtract 1 day to get the actual last day
                        event_end = datetime.combine(event_end - timedelta(days=1), datetime.min.time())
                        event_end = pytz.UTC.localize(event_end)

                    summary = str(component.get('summary', ''))
                    description = str(component.get('description', ''))

                    events.append({
                        'summary': summary,
                        'description': description,
                        'start': event_start,
                        'end': event_end,
                        'uid': str(component.get('uid', '')),
                        'source': calendar_name
                    })

            print(f"  Found {len(events)} events")
            return events

        except Exception as e:
            print(f"  Error fetching {calendar_name}: {e}")
            return []

    def fetch_calendar_events(self, days_ahead: int = 30) -> List[Dict]:
        """Fetch calendar events from all configured sources"""
        all_events = []

        if not self.calendar_url and not self.public_holidays_url:
            print("No calendar URLs configured; skipping calendar event sync")
            return []

        # Fetch from Bizneo calendar
        if self.calendar_url:
            bizneo_events = self.fetch_calendar_from_url(self.calendar_url, "Bizneo calendar")
            all_events.extend(bizneo_events)

        # Fetch from public holidays calendar if configured
        if self.public_holidays_url:
            holidays_events = self.fetch_calendar_from_url(self.public_holidays_url, "Public holidays calendar")
            # Mark all holiday events as public_holiday type
            for event in holidays_events:
                event['force_type'] = 'public_holiday'
            all_events.extend(holidays_events)

        # Filter events within date range
        now = datetime.now(pytz.UTC)
        end_date = now + timedelta(days=days_ahead)

        filtered_events = [
            event for event in all_events
            if event['start'] <= end_date and event['end'] >= now
        ]

        print(f"Total: {len(filtered_events)} events in date range")
        return filtered_events

    def categorize_event(self, event: Dict) -> Optional[str]:
        """Determine the event type based on summary/description"""
        # Check if event has a forced type (e.g., from public holidays calendar)
        if 'force_type' in event:
            return event['force_type']

        summary = event['summary'].lower()
        description = event['description'].lower()
        combined_text = f"{summary} {description}"

        # Check for event types
        if any(keyword in combined_text for keyword in ['sick', 'illness', 'doctor', 'medical']):
            return 'sick_leave'
        elif any(keyword in combined_text for keyword in ['student', 'class', 'course', 'training']):
            return 'student_leave'
        elif any(keyword in combined_text for keyword in ['vacation', 'holiday', 'pto', 'time off']):
            return 'vacation'

        return None

    def get_existing_clickup_tasks(self, date_from: datetime, date_to: datetime) -> List[Dict]:
        """Get existing ClickUp tasks in the date range"""
        url = f"https://api.clickup.com/api/v2/list/{self.clickup_list_id}/task"

        params = {
            'archived': 'false',
            'date_created_gt': int(date_from.timestamp() * 1000),
            'date_created_lt': int(date_to.timestamp() * 1000)
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            tasks = response.json().get('tasks', [])
            print(f"Found {len(tasks)} existing ClickUp tasks")
            return tasks
        except Exception as e:
            print(f"Error fetching ClickUp tasks: {e}")
            return []

    def get_task_by_type(self, event_type: str) -> Optional[str]:
        """Get the existing task ID for a given event type"""
        if not self.clickup_list_id:
            print("  ✗ CLICKUP_LIST_ID is required for event_mappings task lookup")
            return None

        # Get task name from config
        event_config = self.event_mappings.get(event_type, {})
        target_task_name = event_config.get('clickup_task_name')

        if not target_task_name:
            print(f"  ✗ No task name configured for event type: {event_type}")
            return None

        # Search for existing task in the list
        url = f"https://api.clickup.com/api/v2/list/{self.clickup_list_id}/task"
        params = {
            'archived': 'false',
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            tasks = response.json().get('tasks', [])

            # Find the matching task
            for task in tasks:
                if task['name'] == target_task_name:
                    return task['id']

            print(f"  ✗ Task '{target_task_name}' not found in list")
            return None

        except Exception as e:
            print(f"  ✗ Error fetching tasks: {e}")
            return None

    def get_task_by_name(self, task_name: str) -> Optional[str]:
        """Get the existing task ID for a given ClickUp task name"""
        if not task_name:
            return None

        if not self.clickup_list_id:
            print("  ✗ CLICKUP_LIST_ID is required when using task_name lookup")
            return None

        url = f"https://api.clickup.com/api/v2/list/{self.clickup_list_id}/task"
        params = {
            'archived': 'false',
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            tasks = response.json().get('tasks', [])

            for task in tasks:
                if task['name'] == task_name:
                    return task['id']

            print(f"  ✗ Task '{task_name}' not found in list")
            return None

        except Exception as e:
            print(f"  ✗ Error fetching tasks: {e}")
            return None

    def get_existing_time_entries(self, task_id: str, date: datetime, use_custom_task_id: bool = False) -> List[Dict]:
        """Get existing time entries for a task on a specific date"""
        url = f"https://api.clickup.com/api/v2/task/{task_id}/time"
        params = None

        # Support ClickUp custom task IDs (e.g. INF-1353)
        if use_custom_task_id or '-' in task_id:
            if not self.clickup_team_id:
                print("  ⚠ Warning: CLICKUP_TEAM_ID is required for custom task IDs like INF-1353")
                return []
            params = {
                'custom_task_ids': 'true',
                'team_id': self.clickup_team_id
            }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            user_data = response.json().get('data', [])

            # Filter entries for the specific date
            date_start = datetime.combine(date.date(), datetime.min.time())
            date_start = pytz.UTC.localize(date_start)
            date_end = date_start + timedelta(days=1)

            date_entries = []
            # Data is grouped by user, each user has 'intervals'
            for user_entry in user_data:
                intervals = user_entry.get('intervals', [])
                for interval in intervals:
                    interval_start_ms = interval.get('start')
                    if interval_start_ms:
                        interval_start = datetime.fromtimestamp(int(interval_start_ms) / 1000, tz=pytz.UTC)
                        if date_start <= interval_start < date_end:
                            date_entries.append(interval)

            return date_entries

        except Exception as e:
            print(f"  ⚠ Warning: Could not check existing time entries: {e}")
            return []

    def create_time_entry(
        self,
        task_id: str,
        event: Dict,
        date: datetime,
        hours: int = 8,
        skip_weekends: bool = True,
        description: Optional[str] = None,
        use_custom_task_id: bool = False
    ) -> bool:
        """Create a time entry for a specific task and date"""
        # Skip weekends (Saturday=5, Sunday=6) unless explicitly allowed
        if skip_weekends and date.weekday() >= 5:
            print(f"  ⊘ Skipping weekend day {date.strftime('%Y-%m-%d (%A)')}")
            return True  # Return True to not count as failure

        # Check if time entry already exists for this date
        existing_entries = self.get_existing_time_entries(task_id, date, use_custom_task_id=use_custom_task_id)

        if existing_entries:
            print(f"  ⊘ Time entry already exists for {date.strftime('%Y-%m-%d')} (skipping)")
            return True  # Return True since the entry exists

        url = f"https://api.clickup.com/api/v2/task/{task_id}/time"
        params = None

        # Support ClickUp custom task IDs (e.g. INF-1353)
        if use_custom_task_id or '-' in task_id:
            if not self.clickup_team_id:
                print("  ✗ CLICKUP_TEAM_ID is required for custom task IDs like INF-1353")
                return False
            params = {
                'custom_task_ids': 'true',
                'team_id': self.clickup_team_id
            }

        # Create time entry for the full day (8 hours by default)
        # ClickUp expects start and end times, or just duration without start
        start_time = datetime.combine(date.date(), datetime.min.time().replace(hour=9))
        start_time = pytz.UTC.localize(start_time)

        end_time = start_time + timedelta(hours=hours)

        time_entry_data = {
            'start': int(start_time.timestamp() * 1000),
            'end': int(end_time.timestamp() * 1000),
            'description': description if description else f"{event['summary']}"
        }

        try:
            response = requests.post(url, headers=self.headers, params=params, json=time_entry_data)
            response.raise_for_status()
            print(f"  ✓ Added {hours}h time entry for {date.strftime('%Y-%m-%d')}")
            return True
        except Exception as e:
            print(f"  ✗ Error creating time entry: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"    Response: {e.response.text}")
            return False

    def parse_date_string(self, date_string: str) -> Optional[datetime]:
        """Parse a YYYY-MM-DD date string into a UTC datetime"""
        try:
            parsed_date = datetime.strptime(date_string, "%Y-%m-%d")
            return pytz.UTC.localize(parsed_date)
        except ValueError:
            return None

    def build_occupied_dates(self, events: List[Dict]) -> set:
        """Build a set of dates occupied by synced calendar events"""
        occupied_dates = set()

        for event in events:
            if not self.categorize_event(event):
                continue

            current_date = event['start']
            end_date = event['end']

            while current_date <= end_date:
                occupied_dates.add(current_date.date())
                current_date += timedelta(days=1)

        return occupied_dates

    def create_daily_recurring_entries(
        self,
        recurring_task: Dict,
        dry_run: bool = False,
        occupied_dates: Optional[set] = None
    ) -> bool:
        """Create time entries for a specific task repeated every day for a date range"""
        task_id_raw = recurring_task.get('task_id')
        task_id = str(task_id_raw).strip() if task_id_raw else None
        task_name = str(recurring_task.get('task_name', '')).strip()

        if not task_id and not task_name:
            print("  ✗ Provide 'task_id' or 'task_name' in daily_recurring_tasks")
            return False

        start_date_raw = recurring_task.get('start_date')
        if start_date_raw:
            start_date = self.parse_date_string(str(start_date_raw))
            if not start_date:
                print("  ✗ 'start_date' must use YYYY-MM-DD format")
                return False
        else:
            start_date = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        end_date_raw = recurring_task.get('end_date')
        end_date = None
        if end_date_raw:
            end_date = self.parse_date_string(str(end_date_raw))
            if not end_date:
                print("  ✗ 'end_date' must use YYYY-MM-DD format")
                return False

        days = recurring_task.get('days')
        if end_date:
            if end_date < start_date:
                print("  ✗ 'end_date' cannot be before 'start_date'")
                return False

            calculated_days = (end_date - start_date).days + 1
            if isinstance(days, int) and days > 0 and days != calculated_days:
                print("  ⚠ Both 'days' and 'end_date' were provided; using 'end_date'")
            days = calculated_days
        else:
            if not isinstance(days, int) or days <= 0:
                print("  ✗ Provide a positive 'days' or a valid 'end_date' in daily_recurring_tasks")
                return False
            end_date = start_date + timedelta(days=days - 1)

        hours = recurring_task.get('hours', 8)
        try:
            hours = float(hours)
            if hours <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            print("  ✗ 'hours' must be a positive number; using default 8h")
            hours = 8

        include_weekends = bool(recurring_task.get('include_weekends', False))
        use_custom_task_id = bool(recurring_task.get('custom_task_id', False))
        entry_description = str(recurring_task.get('entry_description', '')).strip()
        task_display = task_name if task_name else f"Task ID {task_id}"
        summary = str(recurring_task.get('summary', task_display)).strip() or task_display

        if not dry_run:
            if not task_id:
                task_id = self.get_task_by_name(task_name)
                if not task_id:
                    print("  ✗ Cannot create recurring time entries without an existing task")
                    return False

        event_payload = {'summary': summary}
        created_count = 0
        skipped_count = 0
        failed = False

        for day_offset in range(days):
            current_date = start_date + timedelta(days=day_offset)

            if occupied_dates and current_date.date() in occupied_dates:
                print(
                    f"  ⊘ Skipping occupied day {current_date.strftime('%Y-%m-%d (%A)')} "
                    f"(calendar event exists)"
                )
                skipped_count += 1
                continue

            if dry_run:
                print(
                    f"  [DRY RUN] Would add {hours}h for {current_date.strftime('%Y-%m-%d')} "
                    f"on task '{task_display}'"
                )
                created_count += 1
                continue

            if self.create_time_entry(
                task_id=task_id,
                event=event_payload,
                date=current_date,
                hours=hours,
                skip_weekends=not include_weekends,
                description=entry_description if entry_description else None,
                use_custom_task_id=use_custom_task_id
            ):
                created_count += 1
            else:
                failed = True

        return not failed

    def create_absence_entries(self, event: Dict, event_type: str) -> bool:
        """Create time entries for each day of an absence event"""
        # Get the existing task for this event type
        task_id = self.get_task_by_type(event_type)

        if not task_id:
            print(f"  ✗ Cannot create time entries without a task")
            return False

        # Calculate number of days
        current_date = event['start']
        end_date = event['end']

        success_count = 0
        total_days = (end_date - current_date).days + 1

        # Create a time entry for each day
        while current_date <= end_date:
            if self.create_time_entry(task_id, event, current_date):
                success_count += 1

            current_date += timedelta(days=1)

        return success_count == total_days

    def sync(self, days_ahead: int = 30, dry_run: bool = False):
        """Main sync function"""
        print("="*60)
        print("Starting ClickUp Calendar Sync")
        print("="*60)

        if dry_run:
            print("DRY RUN MODE - No tasks will be created")

        # Fetch calendar events
        events = self.fetch_calendar_events(days_ahead)

        if not events:
            print("No calendar events found to sync")

        # Build a set of dates that have public holidays
        public_holiday_dates = set()
        for event in events:
            if self.categorize_event(event) == 'public_holiday':
                current_date = event['start']
                end_date = event['end']
                while current_date <= end_date:
                    # Only add weekdays to the public holiday set
                    if current_date.weekday() < 5:
                        public_holiday_dates.add(current_date.date())
                    current_date += timedelta(days=1)

        print(f"Found {len(public_holiday_dates)} public holiday dates (excluding weekends)")
        occupied_dates = self.build_occupied_dates(events)
        print(f"Found {len(occupied_dates)} occupied dates from calendar events")

        # Process each event
        created_count = 0
        skipped_count = 0

        for event in events:
            event_type = self.categorize_event(event)

            # Skip vacations that overlap with public holidays
            if event_type == 'vacation':
                # Check if any day of this vacation overlaps with a public holiday
                has_overlap = False
                current_date = event['start']
                end_date = event['end']
                while current_date <= end_date:
                    if current_date.date() in public_holiday_dates:
                        has_overlap = True
                        break
                    current_date += timedelta(days=1)

                if has_overlap:
                    print(f"\nSkipping: {event['summary']} (overlaps with public holiday)")
                    skipped_count += 1
                    continue

            if event_type:
                print(f"\nProcessing: {event['summary']} ({event_type})")

                # Calculate number of days
                days_count = (event['end'] - event['start']).days + 1

                if days_count == 1:
                    print(f"  Date: {event['start'].strftime('%Y-%m-%d')} (1 day)")
                else:
                    print(f"  Date: {event['start'].strftime('%Y-%m-%d')} to {event['end'].strftime('%Y-%m-%d')} ({days_count} days)")

                if not dry_run:
                    if self.create_absence_entries(event, event_type):
                        created_count += 1
                    else:
                        skipped_count += 1
                else:
                    print(f"  [DRY RUN] Would create time entries for each day")
                    created_count += 1
            else:
                print(f"\nSkipping: {event['summary']} (no matching category)")
                skipped_count += 1

        # Process manually configured recurring daily tasks
        if self.daily_recurring_tasks:
            print("\n" + "="*60)
            print("Processing configured daily recurring tasks")
            print("="*60)

            for index, recurring_task in enumerate(self.daily_recurring_tasks, start=1):
                task_name = recurring_task.get('task_name', f"Recurring task #{index}")
                start_date = recurring_task.get('start_date', 'today')
                end_date = recurring_task.get('end_date')
                days = recurring_task.get('days', 'N/A')

                range_text = f"start: {start_date}"
                if end_date:
                    range_text += f", end: {end_date}"
                else:
                    range_text += f", days: {days}"

                print(
                    f"\nRecurring task {index}: {task_name} "
                    f"({range_text})"
                )

                if self.create_daily_recurring_entries(
                    recurring_task,
                    dry_run=dry_run,
                    occupied_dates=occupied_dates
                ):
                    created_count += 1
                else:
                    skipped_count += 1

        print("\n" + "="*60)
        print(f"Sync Complete!")
        print(f"  Events processed: {created_count}")
        print(f"  Events skipped: {skipped_count}")
        print("="*60)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Sync calendar events (sick leave, student leave, vacation, etc.) from Bizneo/iCal to ClickUp time entries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                          # Sync next 30 days
  %(prog)s --days 60                # Sync next 60 days
  %(prog)s --dry-run                # Test without creating entries
  %(prog)s --config custom.json     # Use custom config file

For more information, see: https://github.com/jsacra2003/Bizneo_Clickup
        '''
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        metavar='N',
        help='number of days ahead to sync (default: 30)'
    )
    parser.add_argument(
        '--config',
        type=str,
        default='config.json',
        metavar='FILE',
        help='path to config file (default: config.json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='test run without creating time entries'
    )

    args = parser.parse_args()

    try:
        syncer = ClickUpCalendarSync(config_path=args.config)
        syncer.sync(days_ahead=args.days, dry_run=args.dry_run)
    except FileNotFoundError:
        print(f"Error: Config file '{args.config}' not found")
        print("Please copy config.example.json to config.json and fill in your credentials")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
