"""Schatten agents."""

from agents.calendar_sync import CalendarSync
from agents.deadline_sentinel import DeadlineSentinel
from agents.moodle_watcher import MoodleWatcher
from agents.room_scout import RoomScout
from agents.secretary import Secretary
from agents.social_scout import SocialScout
from agents.study_planner import StudyPlanner

ALL_AGENTS = [
    MoodleWatcher,
    DeadlineSentinel,
    CalendarSync,
    SocialScout,
    StudyPlanner,
    RoomScout,
    Secretary,
]

__all__ = [
    "MoodleWatcher",
    "DeadlineSentinel",
    "CalendarSync",
    "SocialScout",
    "StudyPlanner",
    "RoomScout",
    "Secretary",
    "ALL_AGENTS",
]
