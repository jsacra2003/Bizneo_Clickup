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

        # Validate required environment variables
        if not all([self.clickup_api_key, self.clickup_list_id, self.calendar_url]):
            raise ValueError(
                "Missing required environment variables. Please set:\n"
                "  CLICKUP_API_KEY\n"
                "  CLICKUP_LIST_ID\n"
                "  BIZNEO_CALENDAR_URL\n"
                "Check your .env file."
            )

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

        if not self.event_mappings:
            raise ValueError(
                "No event_mappings found in config file.\n"
                "Please check your config.json has 'sync_settings.event_mappings' configured."
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

        # Fetch from Bizneo calendar
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

    def get_existing_time_entries(self, task_id: str, date: datetime) -> List[Dict]:
        """Get existing time entries for a task on a specific date"""
        url = f"https://api.clickup.com/api/v2/task/{task_id}/time"

        try:
            response = requests.get(url, headers=self.headers)
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

    def create_time_entry(self, task_id: str, event: Dict, date: datetime, hours: int = 8) -> bool:
        """Create a time entry for a specific task and date"""
        # Check if time entry already exists for this date
        existing_entries = self.get_existing_time_entries(task_id, date)

        if existing_entries:
            print(f"  ⊘ Time entry already exists for {date.strftime('%Y-%m-%d')} (skipping)")
            return True  # Return True since the entry exists

        url = f"https://api.clickup.com/api/v2/task/{task_id}/time"

        # Create time entry for the full day (8 hours by default)
        # ClickUp expects start and end times, or just duration without start
        start_time = datetime.combine(date.date(), datetime.min.time().replace(hour=9))
        start_time = pytz.UTC.localize(start_time)

        end_time = start_time + timedelta(hours=hours)

        time_entry_data = {
            'start': int(start_time.timestamp() * 1000),
            'end': int(end_time.timestamp() * 1000),
            'description': f"{event['summary']}"
        }

        try:
            response = requests.post(url, headers=self.headers, json=time_entry_data)
            response.raise_for_status()
            print(f"  ✓ Added {hours}h time entry for {date.strftime('%Y-%m-%d')}")
            return True
        except Exception as e:
            print(f"  ✗ Error creating time entry: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"    Response: {e.response.text}")
            return False

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
            print("No events found to sync")
            return

        # Process each event
        created_count = 0
        skipped_count = 0

        for event in events:
            event_type = self.categorize_event(event)

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
