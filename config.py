"""
config.py — all settings loaded from environment / .env file.
Copy .env.example to .env and fill in values before running.
"""
import os
import warnings
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
load_dotenv(Path.home() / '.secrets/meetingnotes.env', override=True)  # secrets win


def _warn_if_missing(value: str, var: str, description: str) -> str:
    if not value:
        warnings.warn(
            f"{var} is not set — {description} will not work. Set {var} in .env.",
            stacklevel=2,
        )
    return value


# Storage
RAW_DIR  = Path(os.environ.get('NB_RAW_DIR',  '/var/lib/meetingnotes/raw'))
DB_PATH  = Path(os.environ.get('NB_DB_PATH',  '/var/lib/meetingnotes/provenance.db'))

# Pad source — required; no default so the specific pad URL stays out of source
PAD_URL = _warn_if_missing(
    os.environ.get('NB_PAD_URL', ''), 'NB_PAD_URL', 'pad fetching',
)

# Wiki — required for publishing; warn if missing
WIKI_API_URL  = _warn_if_missing(os.environ.get('NB_WIKI_API_URL', ''),  'NB_WIKI_API_URL',  'wiki API access')
WIKI_PAGE_URL = os.environ.get('NB_WIKI_PAGE_URL', '')
WIKI_EU_URL   = os.environ.get('NB_WIKI_EU_URL', '')   # optional mirror; leave blank to hide in UI

# Wiki bot credentials (for publishing)
WIKI_BOT_USER = os.environ.get('WIKI_BOT_USER', '')
WIKI_BOT_PASS = os.environ.get('WIKI_BOT_PASSWORD', '')

# Preview publish prefix
WIKI_PREVIEW_PREFIX = os.environ.get('NB_WIKI_PREVIEW_PREFIX', 'User:Bot/Meeting_Notes_')

# Organization name — used in UI labels and AI summary prompts
ORG_NAME = os.environ.get('NB_ORG_NAME', '')

# Wiki footer elements appended to every published page (leave blank to skip)
WIKI_YEAR_BANNER = os.environ.get('NB_WIKI_YEAR_BANNER', '')  # e.g. {{meetings2026}}
WIKI_CATEGORY    = os.environ.get('NB_WIKI_CATEGORY', '')     # e.g. Category:Meeting Notes

# Pattern for "Nth Meeting of <org>" link in wiki pages — used to resolve meeting
# ordinals. Set to the phrase inside the link, e.g. "Meeting of YourOrg".
# Leave blank to skip meeting-number resolution.
WIKI_MEETING_NUM_PATTERN = os.environ.get('NB_WIKI_MEETING_NUM_PATTERN', '')

# Meeting notes template page on the wiki; monitored for changes.
# Leave blank to disable template-change warnings.
WIKI_TEMPLATE_TITLE = os.environ.get('NB_WIKI_TEMPLATE_TITLE', '')

# HTTP User-Agent for all outbound requests
USER_AGENT = os.environ.get('NB_USER_AGENT', 'MeetingNotesBot/1.0')

# Web app
SECRET_KEY = os.environ.get('NB_SECRET_KEY', 'change-me-in-production')
if SECRET_KEY == 'change-me-in-production':
    warnings.warn(
        "NB_SECRET_KEY is not set — using insecure default. "
        "Set NB_SECRET_KEY in .env before running in production.",
        stacklevel=2,
    )

# Processor: path to the script that transforms raw → wiki text.
PROCESSOR_SCRIPT = Path(
    os.environ.get('NB_PROCESSOR_SCRIPT',
                   Path(__file__).parent / 'pipeline' / 'process.py')
)
