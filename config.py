"""
config.py — all settings loaded from environment / .env file.
Copy .env.example to .env and fill in values before running.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
load_dotenv(Path.home() / '.secrets/nbwiki.env', override=True)  # secrets win

# Storage
RAW_DIR  = Path(os.environ.get('NB_RAW_DIR',  '/var/lib/nbmeetingnotes/raw'))
DB_PATH  = Path(os.environ.get('NB_DB_PATH',  '/var/lib/nbmeetingnotes/provenance.db'))

# Pad source — required; no default so the specific pad URL stays out of source
_pad_url = os.environ.get('NB_PAD_URL', '')
if not _pad_url:
    import warnings
    warnings.warn(
        "NB_PAD_URL is not set — pad fetching will fail. "
        "Set NB_PAD_URL in .env to your meeting notes pad export URL.",
        stacklevel=2,
    )
PAD_URL = _pad_url

# Wiki
WIKI_API_URL  = os.environ.get('NB_WIKI_API_URL',  'https://www.noisebridge.net/api.php')
WIKI_PAGE_URL = os.environ.get('NB_WIKI_PAGE_URL', 'https://www.noisebridge.net/wiki')
WIKI_EU_URL   = os.environ.get('NB_WIKI_EU_URL',   'https://www.noisebridge.eu/wiki')

# Wiki bot credentials (for publishing processed notes)
WIKI_BOT_USER = os.environ.get('NOISEBRIDGE_WIKI_USER', '')
WIKI_BOT_PASS = os.environ.get('NOISEBRIDGE_WIKI_PASSWORD', '')

# Preview publishes go here; final publishes go to Meeting_Notes_YYYY_MM_DD
WIKI_PREVIEW_PREFIX = os.environ.get('NB_WIKI_PREVIEW_PREFIX', 'User:Bot/Meeting_Notes_')

# AI
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# HTTP User-Agent for all outbound requests (wiki API, pad fetches).
# Include a contact URL so wiki admins can identify the bot.
USER_AGENT = os.environ.get('NB_USER_AGENT', 'NBArchive/1.0')

# Meeting notes template page on the wiki. The pipeline's artifact-removal rules
# in transforms.py were written against a specific version of this template.
# If the template changes, the app will display a warning.
WIKI_TEMPLATE_TITLE = os.environ.get(
    'NB_WIKI_TEMPLATE_TITLE', 'Meeting Notes Template'
)

# Web app
SECRET_KEY = os.environ.get('NB_SECRET_KEY', 'change-me-in-production')
if SECRET_KEY == 'change-me-in-production':
    import warnings
    warnings.warn(
        "NB_SECRET_KEY is not set — using insecure default. "
        "Set NB_SECRET_KEY in .env before running in production.",
        stacklevel=2,
    )

# Processor: path to the Python script that transforms raw → wiki text.
# Script must accept --date YYYY_MM_DD --input <path> and write result to stdout.
# Metrics may optionally be emitted as a JSON line to stderr prefixed "METRICS:".
PROCESSOR_SCRIPT = Path(
    os.environ.get('NB_PROCESSOR_SCRIPT',
                   Path(__file__).parent / 'pipeline' / 'process.py')
)
