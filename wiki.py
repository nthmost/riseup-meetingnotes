"""
wiki.py — thin wrapper around the MediaWiki Action API.

Handles session cookies (via CookieJar), login, and page edits.
Raises on network errors; callers decide how to handle failures.
"""
import http.cookiejar
import json
import time  # used in 429 retry backoff
import urllib.error
import urllib.parse
import urllib.request

import config


class WikiAPI:
    def __init__(self, api_url: str, user_agent: str = config.USER_AGENT):
        self.api_url    = api_url
        self.user_agent = user_agent
        jar             = http.cookiejar.CookieJar()
        self._opener    = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

    def call(self, params: dict, post: dict | None = None, _retries: int = 3) -> dict:
        params['format'] = 'json'
        url  = self.api_url + '?' + urllib.parse.urlencode(params)
        data = urllib.parse.urlencode(post).encode('utf-8') if post else None
        req  = urllib.request.Request(
            url, data=data, headers={'User-Agent': self.user_agent}
        )
        for attempt in range(_retries + 1):
            try:
                with self._opener.open(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < _retries:
                    wait = int(e.headers.get('Retry-After', 2 ** (attempt + 1)))
                    time.sleep(wait)
                    continue
                raise

    def login(self, username: str, password: str) -> str:
        """
        Log in with username + password.
        Returns 'Success' on success, or the MediaWiki failure reason string.
        Raises urllib.error.HTTPError / Exception on network errors.
        """
        token_resp   = self.call({'action': 'query', 'meta': 'tokens', 'type': 'login'})
        login_token  = token_resp['query']['tokens']['logintoken']
        login_resp   = self.call(
            {'action': 'login'},
            post={'lgname': username, 'lgpassword': password, 'lgtoken': login_token},
        )
        result = login_resp.get('login', {}).get('result', '')
        if result == 'Success':
            return 'Success'
        return login_resp.get('login', {}).get('reason', result or 'Unknown error')

    def edit_page(self, title: str, content: str, summary: str) -> None:
        """
        Create or overwrite a wiki page.
        Must be logged in first (same WikiAPI instance).
        Raises on network or API error.
        """
        csrf_resp = self.call({'action': 'query', 'meta': 'tokens'})
        csrf      = csrf_resp['query']['tokens']['csrftoken']
        self.call({}, post={
            'action':  'edit',
            'title':   title,
            'text':    content,
            'summary': summary,
            'token':   csrf,
        })
