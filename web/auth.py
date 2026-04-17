"""
web/auth.py — authentication via the MediaWiki API.

Users log in with their NB wiki username + password. We verify against
the wiki API, then store the username in a signed Flask session cookie.
We never store or forward the password beyond the one verification request.
"""
import logging
import urllib.error
from functools import wraps

from flask import redirect, request, session, url_for

import config
from wiki import WikiAPI

log = logging.getLogger(__name__)


def verify_wiki_credentials(username: str, password: str) -> tuple[bool, str]:
    """
    Verify username + password against the NB wiki login API.
    Returns (success: bool, error_message: str).
    """
    try:
        result = WikiAPI(config.WIKI_API_URL).login(username, password)
        if result == 'Success':
            return True, ''
        log.warning('Wiki login failed for %r: %s', username, result)
        return False, result
    except urllib.error.HTTPError as e:
        msg = f'Wiki API returned HTTP {e.code}'
        log.warning(msg)
        return False, msg
    except Exception as e:
        msg = f'Could not reach wiki: {e}'
        log.warning(msg)
        return False, msg


def current_user() -> str | None:
    return session.get('wiki_user')


def login_required(f):
    """Route decorator: redirects to /login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated
