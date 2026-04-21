"""
org_pipeline/ai.py — AI summary generation.

This is the ONLY module in the pipeline that imports anthropic.
Everything else is deterministic text transforms or network I/O.

Public API:
    generate_summary(content_text) -> (summary_text | None, metrics | None)
"""
import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


def fetch_membership_levels() -> str:
    """
    Fetch current membership tier names from the wiki (NB_WIKI_MEMBERSHIP_PAGE).
    Used only to build the AI summary prompt context.

    Set NB_WIKI_MEMBERSHIP_PAGE to the wiki page title that lists membership tiers.
    Set NB_WIKI_MEMBERSHIP_FALLBACK to a plain-text description used when the
    fetch fails or the page is not configured.

    Returns a brief context string for inclusion in the AI prompt.
    """
    fallback = os.getenv(
        'NB_WIKI_MEMBERSHIP_FALLBACK',
        'Membership tiers are defined by the organisation.'
    )
    membership_page = os.getenv('NB_WIKI_MEMBERSHIP_PAGE', '')
    wiki_api_url = os.getenv('NB_WIKI_API_URL', '')

    if not membership_page or not wiki_api_url:
        return fallback

    try:
        params = {
            'action': 'query', 'prop': 'revisions',
            'titles': membership_page,
            'rvprop': 'content', 'rvslots': 'main',
            'format': 'json',
        }
        url = wiki_api_url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': os.getenv('NB_USER_AGENT', 'MeetingNotesBot/1.0')})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        page = next(iter(data['query']['pages'].values()))
        if 'missing' in page or not page.get('revisions'):
            return fallback
        content = (page['revisions'][0].get('slots', {}).get('main', {}).get('*')
                   or page['revisions'][0].get('*', ''))
        lines = [l.strip() for l in content.splitlines()
                 if l.strip() and not l.strip().startswith('=') and not l.strip().startswith('{')]
        snippet = ' '.join(lines[:8])
        org_name = os.getenv('NB_ORG_NAME', 'the organisation')
        return f"Current {org_name} membership tier names (from wiki): {snippet}"
    except Exception as e:
        log.warning(f"could not fetch membership levels from wiki: {e}; using fallback")
        return fallback


def generate_summary(content_text: str) -> tuple[str | None, dict | None]:
    """
    Use Claude Haiku to generate the Meeting Summary section.

    Returns (summary_text, metrics) where metrics contains 'model_name'
    and 'token_usage', or (None, None) if generation was skipped or failed.

    The summary is returned for review — it is NOT inserted into the content
    by this function. Insertion is a separate step triggered by the user.
    """
    if not _ANTHROPIC_AVAILABLE:
        log.warning("anthropic package not installed; skipping AI summary")
        return None, None

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set; skipping AI summary")
        return None, None

    client = _anthropic.Anthropic(api_key=api_key)
    membership_context = fetch_membership_levels()
    org_name = os.getenv('NB_ORG_NAME', 'our organisation')

    # Cap input to avoid runaway cost/latency on unexpectedly large documents
    max_chars = int(os.getenv('NB_AI_MAX_CHARS', '50000'))
    if len(content_text) > max_chars:
        log.warning(f"content_text truncated from {len(content_text)} to {max_chars} chars for AI summary")
        content_text = content_text[:max_chars]

    prompt = f"""You are producing the official public summary for a {org_name} meeting wiki page. This summary is emailed to the mailing list and posted to chat/Discord.

Read the meeting notes below carefully and write the full Meeting Summary.

STRICT RULES — these are non-negotiable:
1. Factual extractions only. Do NOT paraphrase dialogue, invent, infer, or embellish.
2. If a category has nothing to report, write "None".
3. Use MediaWiki markup throughout: bold is '''text''' not **text**. No Markdown.
4. NEVER include URLs anywhere. Drop them silently.
5. Be concise. Names and outcomes only — no backstory, no dates, no context.
6. For new members and associates, include their membership type in parentheses if stated. Use these current tier names: {membership_context}
7. CRITICAL — Consensus Items: items are PROPOSED at one meeting and only pass if explicitly confirmed with no blocks. "Resolved, that..." is proposal language, NOT passage. Prefix with "proposed:" or "passed:".
8. CRITICAL — Announcements: ONLY items explicitly listed under the Announcements section of the notes (upcoming events, external notices, brief shout-outs). Do NOT put equipment proposals, space improvement ideas, access policy discussions, or anything from the Discussion section here. When in doubt, leave it out of Announcements.
9. CRITICAL — TLDR top sentence: only whole-organisation matters (elections, major financial decisions, membership policy changes, significant consensus). Do NOT summarise regular discussion topics here. If nothing qualifies, one plain sentence describing what the meeting covered overall.
10. CRITICAL — Discussion Items: headline format only. Topic + outcome + action/contact if one exists. Hard limit: 12 words per item. Do NOT explain, give context, or list multiple outcomes. If there is a named next step or contact person, end with it.

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

    log.info("generating summary via Claude Haiku")
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
    block = message.content[0]
    if not hasattr(block, 'text'):
        log.warning(f"unexpected content block type from API: {type(block).__name__}; skipping summary")
        return None, metrics
    return block.text.strip(), metrics
