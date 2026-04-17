"""
noisebridge_pipeline/ai.py — AI summary generation.

This is the ONLY module in the pipeline that imports anthropic.
Everything else is deterministic text transforms or network I/O.

Public API:
    generate_summary(content_text) -> (summary_text | None, metrics | None)
"""
import json
import os
import sys
import urllib.parse
import urllib.request

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


def fetch_membership_levels() -> str:
    """
    Fetch current Noisebridge membership tier names from the wiki.
    Used only to build the AI summary prompt — not part of the
    deterministic pipeline.

    Returns a brief context string. Falls back to hardcoded description
    on any network or parse failure.
    """
    _FALLBACK = (
        "Noisebridge membership tiers (current as of 2026): "
        "Core Member (formerly 'Big M' Member) — full consensus rights, pays dues, 24-hour access. "
        "Access Member (formerly 'Associate' or 'little m' Member) — entry-level membership. "
        "Philanthropist — supporter/donor membership."
    )
    wiki_api_url = os.getenv('NB_WIKI_API_URL', 'https://www.noisebridge.net/api.php')
    try:
        params = {
            'action': 'query', 'prop': 'revisions',
            'titles': 'Membership',
            'rvprop': 'content', 'rvslots': 'main',
            'rvsection': '4',
            'format': 'json',
        }
        url = wiki_api_url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': os.getenv('NB_USER_AGENT', 'NBArchive/1.0')})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        page = next(iter(data['query']['pages'].values()))
        if 'missing' in page or not page.get('revisions'):
            return _FALLBACK
        content = (page['revisions'][0].get('slots', {}).get('main', {}).get('*')
                   or page['revisions'][0].get('*', ''))
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and not l.strip().startswith('=') and not l.strip().startswith('{')]
        snippet = ' '.join(lines[:8])
        return f"Current Noisebridge membership tier names (from wiki): {snippet}"
    except Exception as e:
        print(f"Warning: could not fetch membership levels from wiki ({e}); using fallback.",
              file=sys.stderr)
        return _FALLBACK


def generate_summary(content_text: str) -> tuple[str | None, dict | None]:
    """
    Use Claude Haiku to generate the Meeting Summary section.

    Returns (summary_text, metrics) where metrics contains 'model_name'
    and 'token_usage', or (None, None) if generation was skipped or failed.

    The summary is returned for review — it is NOT inserted into the content
    by this function. Insertion is a separate step triggered by the user.
    """
    if not _ANTHROPIC_AVAILABLE:
        print("Warning: 'anthropic' package not installed. Skipping AI summary.",
              file=sys.stderr)
        return None, None

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("Warning: ANTHROPIC_API_KEY not set. Skipping AI summary.", file=sys.stderr)
        return None, None

    client = _anthropic.Anthropic(api_key=api_key)
    membership_context = fetch_membership_levels()

    prompt = f"""You are producing the official public summary for a Noisebridge hackerspace meeting wiki page. This summary is emailed to the mailing list and posted to Discord.

Read the meeting notes below carefully and write the full Meeting Summary.

STRICT RULES — these are non-negotiable:
1. Factual extractions only. Do NOT paraphrase dialogue, invent, infer, or embellish.
2. If a category has nothing to report, write "None".
3. Use MediaWiki markup throughout: bold is '''text''' not **text**. No Markdown.
4. NEVER include URLs anywhere. Drop them silently.
5. Be concise. Names and outcomes only — no backstory, no dates, no context. "Loren forfeited duel to Naomi", not "Naomi declared forfeit in duel with Loren (challenged February 24th, no follow-up in 6 weeks)".
6. For new members and associates, include their membership type in parentheses if stated. Use these current tier names: {membership_context}
7. CRITICAL — Consensus Items: items are PROPOSED at one meeting and only pass if explicitly confirmed with no blocks. "Resolved, that..." is proposal language, NOT passage. Prefix with "proposed:" or "passed:".
8. CRITICAL — Announcements: ONLY items explicitly listed under the Announcements section of the notes (upcoming events, external notices, brief shout-outs). Do NOT put equipment proposals, space improvement ideas, access policy discussions, or anything from the Discussion section here. When in doubt, leave it out of Announcements.
9. CRITICAL — TLDR top sentence: only whole-organisation matters (board elections, major financial decisions, membership policy changes, significant consensus). Do NOT summarise regular discussion topics here. If nothing qualifies, one plain sentence describing what the meeting covered overall.
10. CRITICAL — Discussion Items: headline format only. Topic + outcome + action/contact if one exists. Hard limit: 12 words per item. Do NOT explain, give context, or list multiple outcomes. If there is a named next step or contact person, end with it: "contact Elan", "Chris to cost out", "LX coordinating", etc.
    WRONG: "Laser cutter relocation: Laser was moved without proper communication and reconnection; JD will ensure laser is restored before Sunday; larger moves need consensus."
    RIGHT: "Laser cutter: restore before Sunday class; contact JD."
    WRONG: "Morning access schedule: Ken clarified policy—people without access can be left in building during operation hours only, cannot leave or admit others."
    RIGHT: "Morning access policy: operation-hours rules clarified."
    WRONG: "Afterlight Echo pt2: Jane needs written permission from Noisebridge or property manager to hold event; contact Elan."
    RIGHT: "Afterlight Echo: indoor event permission needed; contact Elan."

OUTPUT FORMAT — use this exactly, including the *# markup for Discussion Items:

'''[One sentence. Whole-org highlights only; or plain summary of what the meeting covered.]'''

* '''Announcements:''' [Announcements section items only, semicolon-separated; or None]
* '''Fundraising Update:''' [fundraising news; or None]
* '''Finances:''' [figures and financial items; or None]
* '''New members:''' [names with type in parens; or None]
* '''New associates:''' [names with type in parens; or None]
* '''Consensus Items:''' [1. proposed/passed: item; or None]
* '''Discussion Items:'''
*# [Topic: outcome. 12 words max.]
*# [Topic: outcome. 12 words max.]

Meeting notes:
{content_text}"""

    print("Generating summary via Claude Haiku...", file=sys.stderr)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    metrics = {
        'model_name': message.model,
        'token_usage': {
            'input_tokens':  message.usage.input_tokens,
            'output_tokens': message.usage.output_tokens,
        } if hasattr(message, 'usage') else None,
    }
    return message.content[0].text.strip(), metrics
