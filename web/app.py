"""
web/app.py — Flask application for nbmeetingnotes archive viewer.
"""
import concurrent.futures
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_pad_check_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='pad-check')

sys.path.insert(0, str(Path(__file__).parent.parent))

from urllib.parse import urlparse

from flask import (Flask, Response, abort, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_wtf.csrf import CSRFProtect

import config
import db
from pipeline.publish import publish as _publish
from pipeline.run import fetch_only, insert_summary_pass
from web.auth import current_user, login_required, verify_wiki_credentials
from web.worker import start as _start_worker

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')
app.secret_key = config.SECRET_KEY
app.jinja_env.filters['from_json'] = json.loads
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = None  # tokens valid for session lifetime

csrf = CSRFProtect(app)

db.init()  # ensure schema exists (idempotent)
_start_worker()

ISSUE_LABELS = {
    'attribution': 'Attribution',
    'formatting':  'Formatting',
    'spelling':    'Spelling / typos',
    'missing_data': 'Missing data',
}


# ── template helpers ───────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    template_alert = None
    if db.template_has_unacknowledged_change():
        template_alert = db.get_template_change_details()
    return {
        'current_user': current_user(),
        'issue_labels': ISSUE_LABELS,
        'template_alert': template_alert,
        'config': config,
    }


def _fmt_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, '%Y_%m_%d')
        return dt.strftime('%B ') + str(dt.day) + dt.strftime(', %Y')
    except ValueError:
        return date_str


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return '—'
    return iso[:16].replace('T', ' ') + ' UTC'


def _fmt_size(n: int) -> str:
    return f'{n // 1024} KB' if n >= 1024 else f'{n} B'


def _enrich_capture(row) -> dict:
    d = dict(row)
    d['_date_pretty'] = _fmt_date(d['meeting_date'])
    d['_fetched_pretty'] = _fmt_ts(d.get('captured_at'))
    d['_size_pretty'] = _fmt_size(d.get('size_bytes', 0))
    d['_raw_exists'] = Path(d['file_path']).exists()
    d['_locked'] = bool(d.get('locked'))
    d['_wiki_revisions'] = d.get('wiki_revisions')
    return d


def _enrich_txn(row) -> dict:
    d = dict(row)
    d['_run_at_pretty'] = _fmt_ts(d.get('run_at'))
    d['_published_at_pretty'] = _fmt_ts(d.get('published_at'))
    d['_flags'] = json.loads(d.get('flags') or '{}')
    d['_token_usage'] = json.loads(d.get('token_usage') or 'null')
    d['_output_exists'] = bool(d.get('output_path')) and Path(d['output_path']).exists()
    d['_rating'] = db.get_summary_rating(d['id'])
    d['_is_final_published'] = bool(d.get('published_to'))
    d['_is_preview_published'] = bool(d.get('preview_published_to'))
    d['_generated_summary'] = d.get('generated_summary')
    return d


# ── routes: public ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    captures = [_enrich_capture(r) for r in db.list_captures()]
    for c in captures:
        txn = db.get_latest_transformation(c['id'])
        c['_latest_txn'] = _enrich_txn(txn) if txn else None
        c['_pass_count'] = len(db.get_transformations_for_capture(c['id']))
        txn = c['_latest_txn']
        c['_needs_attention'] = not txn or not txn['_is_final_published']
    return render_template('index.html', captures=captures)


@app.route('/view/<date_str>')
def view(date_str: str):
    capture = db.get_capture_by_date(date_str)
    if not capture:
        abort(404)
    capture = _enrich_capture(capture)

    # If a pipeline job was just queued, check its status.
    # Redirect straight to the result if it finished before the page loaded.
    job_id = request.args.get('job', type=int)
    job = None
    if job_id:
        job_row = db.get_job(job_id)
        if job_row:
            if job_row['status'] == 'done' and job_row['result_txn_id']:
                return redirect(url_for('view', date_str=date_str,
                                        txn=job_row['result_txn_id']))
            job = dict(job_row)

    # Support ?txn=<id> to view a specific transformation
    txn_id = request.args.get('txn', type=int)
    all_txns = [_enrich_txn(t) for t in db.get_transformations_for_capture(capture['id'])]
    if not all_txns:
        txn = None
    elif txn_id:
        txn = next((t for t in all_txns if t['id'] == txn_id), all_txns[-1])
    else:
        txn = all_txns[-1]

    raw_content = None
    if capture['_raw_exists']:
        raw_content = Path(capture['file_path']).read_text('utf-8')

    output_content = None
    if txn and txn['_output_exists']:
        output_content = Path(txn['output_path']).read_text('utf-8')

    user_rating = None
    if txn and current_user():
        ratings = db.get_ratings_for_transformation(txn['id'])
        user_rating = next(
            (dict(r) for r in ratings if r['rated_by'] == current_user()), None
        )

    return render_template(
        'view.html',
        capture=capture,
        txn=txn,
        all_txns=all_txns,
        raw_content=raw_content,
        output_content=output_content,
        user_rating=user_rating,
        job=job,
        wiki_net=f"{config.WIKI_PAGE_URL}/Meeting_Notes_{date_str}",
        wiki_eu=f"{config.WIKI_EU_URL}/Meeting_Notes_{date_str}",
        preview_url=f"{config.WIKI_PAGE_URL}/{txn['preview_published_to'].replace(' ','_')}"
                    if txn and txn.get('preview_published_to') else None,
        preview_title=f"{config.WIKI_PREVIEW_PREFIX}{date_str}",
    )


@app.route('/history/<date_str>')
def history(date_str: str):
    capture = db.get_capture_by_date(date_str)
    if not capture:
        abort(404)
    capture = _enrich_capture(capture)
    txns = [_enrich_txn(t) for t in db.get_transformations_for_capture(capture['id'])]
    for txn in txns:
        txn['_ratings'] = [dict(r) for r in db.get_ratings_for_transformation(txn['id'])]
    return render_template('history.html', capture=capture, txns=txns)


@app.route('/raw/<date_str>')
def raw(date_str: str):
    capture = db.get_capture_by_date(date_str)
    if not capture:
        abort(404)
    path = Path(capture['file_path'])
    if not path.exists():
        abort(404)
    return Response(
        path.read_text('utf-8'),
        mimetype='text/plain; charset=utf-8',
        headers={'Content-Disposition': f'inline; filename="raw_{date_str}.txt"'},
    )


@app.route('/robots.txt')
def robots():
    return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')


# ── routes: rating (login required) ───────────────────────────────────────────

@app.route('/rate/<int:txn_id>', methods=['POST'])
@login_required
def rate(txn_id: int):
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)

    rating = request.form.get('rating')
    if rating not in ('up', 'down'):
        abort(400)

    issues = request.form.getlist('issues')
    invalid = set(issues) - set(ISSUE_LABELS)
    if invalid:
        abort(400)

    db.upsert_rating(
        transformation_id=txn_id,
        rated_at=datetime.now(timezone.utc).isoformat(),
        rated_by=current_user(),
        rating=rating,
        issues=issues,
        problem_excerpt=request.form.get('problem_excerpt', '').strip() or None,
        problem_description=request.form.get('problem_description', '').strip() or None,
    )

    capture = db.get_capture_by_id(txn['raw_capture_id'])
    return redirect(url_for('view', date_str=capture['meeting_date'], txn=txn_id,
                            rated=rating, _anchor='rating'))


@app.route('/detect-published/<int:capture_id>', methods=['POST'])
@login_required
def detect_published(capture_id: int):
    """Check wiki for a pre-existing published page and record it if found."""
    capture = db.get_capture_by_id(capture_id)
    if not capture:
        abort(404)
    fetch_only(capture['meeting_date'])
    return redirect(url_for('view', date_str=capture['meeting_date']))


@app.route('/process/<int:capture_id>', methods=['POST'])
@login_required
def process_capture(capture_id: int):
    """Enqueue a pipeline run on a raw capture."""
    capture = db.get_capture_by_id(capture_id)
    if not capture:
        abort(404)
    job_id = db.insert_job(
        capture_id=capture_id,
        parent_txn_id=None,
        flags={'generate_ai_summary': False},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/dismiss-summary/<int:txn_id>', methods=['POST'])
@login_required
def dismiss_summary(txn_id: int):
    """Clear a pending generated summary so the review modal does not reappear."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    db.clear_generated_summary(txn_id)
    return redirect(url_for('view', date_str=capture['meeting_date'], txn=txn_id))


@app.route('/apply-summary/<int:txn_id>', methods=['POST'])
@login_required
def apply_summary(txn_id: int):
    """Insert the (possibly edited) AI summary into the output, creating a new pass."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    if not txn['output_path'] or not Path(txn['output_path']).exists():
        abort(400, 'No output file for this transformation')
    summary_text = request.form.get('summary_text', '').strip()
    if not summary_text:
        abort(400, 'Summary text is required')
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    new_txn_id = insert_summary_pass(txn_id, summary_text)
    # Auto-preview the new pass
    preview_title = f"{config.WIKI_PREVIEW_PREFIX}{capture['meeting_date']}"
    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=None,
        flags={'job_type': 'publish_preview', 'txn_id': new_txn_id,
               'preview_title': preview_title},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/generate-summary/<int:txn_id>', methods=['POST'])
@login_required
def generate_summary_route(txn_id: int):
    """Enqueue an AI summary generation job for an existing processed output."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    if not txn['output_path'] or not Path(txn['output_path']).exists():
        abort(400, 'No output file for this transformation')
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=None,
        flags={'job_type': 'generate_summary', 'txn_id': txn_id},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/publish-preview/<int:txn_id>', methods=['POST'])
@login_required
def publish_preview(txn_id: int):
    """Enqueue a preview publish job (runs in background worker)."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    if not txn['output_path'] or not Path(txn['output_path']).exists():
        abort(400, 'No output file for this transformation')
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    preview_title = f"{config.WIKI_PREVIEW_PREFIX}{capture['meeting_date']}"
    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=None,
        flags={'job_type': 'publish_preview', 'txn_id': txn_id,
               'preview_title': preview_title},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/rerun/<int:txn_id>', methods=['POST'])
@login_required
def rerun(txn_id: int):
    """Enqueue a re-run of the pipeline using the same raw capture."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    flags = json.loads(txn['flags'] or '{}')
    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=txn_id,
        flags=flags,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/publish/<int:txn_id>', methods=['POST'])
@login_required
def publish(txn_id: int):
    """Enqueue a publish job (runs in background worker)."""
    txn = db.get_transformation(txn_id)
    if not txn:
        abort(404)
    if not txn['output_path'] or not Path(txn['output_path']).exists():
        abort(400, 'No output file for this transformation')
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=None,
        flags={'job_type': 'publish', 'txn_id': txn_id},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return redirect(url_for('view', date_str=capture['meeting_date'], job=job_id))


@app.route('/acknowledge-template-change', methods=['POST'])
@login_required
def acknowledge_template_change():
    """Mark the current template version as acknowledged, clearing the warning."""
    db.acknowledge_template_changes()
    return redirect(request.referrer or url_for('index'))


@app.route('/toggle-lock/<int:capture_id>', methods=['POST'])
@login_required
def toggle_lock(capture_id: int):
    capture = db.get_capture_by_id(capture_id)
    if not capture:
        abort(404)
    new_locked = not bool(capture['locked'])
    db.set_capture_locked(capture_id, locked=new_locked)
    return redirect(url_for('view', date_str=capture['meeting_date']))


# ── pad-update check ───────────────────────────────────────────────────────────

def _next_tuesday(d: date) -> date:
    """Return the first Tuesday strictly after d."""
    days = (1 - d.weekday()) % 7 or 7
    return d + timedelta(days=days)


def _fetch_pad_sha(source_url: str) -> str:
    """Fetch the Riseup Pad and return its SHA-256. Runs in a thread pool."""
    req = urllib.request.Request(
        source_url,
        headers={'User-Agent': config.USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return hashlib.sha256(resp.read()).hexdigest()


@app.route('/api/check-update/<date_str>')
def check_update(date_str: str):
    """Return JSON indicating whether the Riseup Pad has changed since capture.

    The pad fetch runs in a thread pool so the gunicorn worker is not blocked
    for the full request duration.
    """
    capture = db.get_capture_by_date(date_str)
    if not capture:
        return jsonify({'update_available': False, 'reason': 'not_found'})

    try:
        meeting_dt = datetime.strptime(date_str, '%Y_%m_%d').date()
    except ValueError:
        return jsonify({'update_available': False, 'reason': 'invalid_date'})

    if date.today() >= _next_tuesday(meeting_dt):
        return jsonify({'update_available': False, 'reason': 'next_meeting_underway'})

    try:
        future = _pad_check_pool.submit(_fetch_pad_sha, capture['source_url'])
        current_sha = future.result(timeout=5)
    except concurrent.futures.TimeoutError:
        return jsonify({'update_available': False, 'reason': 'fetch_timeout'})
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        app.logger.warning('check_update: pad fetch failed for %s: %s', date_str, e)
        return jsonify({'update_available': False, 'reason': 'fetch_error'})

    if current_sha == capture['sha256']:
        return jsonify({'update_available': False, 'reason': 'no_change'})

    return jsonify({'update_available': True})


@app.route('/api/job-status/<int:job_id>')
@login_required
def job_status(job_id: int):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    return jsonify({
        'status': job['status'],
        'result_txn_id': job['result_txn_id'],
        'error': job['error'],
    })


@app.route('/api/refresh-raw/<date_str>', methods=['POST'])
@login_required
def refresh_raw(date_str: str):
    """Enqueue a pad re-fetch job. Returns {job_id} immediately; caller polls status."""
    capture = db.get_capture_by_date(date_str)
    if not capture:
        abort(404)

    try:
        meeting_dt = datetime.strptime(date_str, '%Y_%m_%d').date()
    except ValueError:
        abort(400)

    if date.today() >= _next_tuesday(meeting_dt):
        return jsonify({'success': False, 'error': 'next_meeting_underway'}), 400

    file_path = Path(capture['file_path'])
    if not file_path.exists():
        return jsonify({'success': False, 'error': 'raw file missing from disk'}), 404

    job_id = db.insert_job(
        capture_id=capture['id'],
        parent_txn_id=None,
        flags={'job_type': 'refresh_raw', 'date_str': date_str},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return jsonify({'job_id': job_id})


# ── routes: auth ───────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            error = 'Username and password are required.'
        else:
            ok, reason = verify_wiki_credentials(username, password)
            if ok:
                session.permanent = True
                session['wiki_user'] = username
                raw_next = request.form.get('next', '')
                parsed = urlparse(raw_next)
                next_url = raw_next if (raw_next and not parsed.netloc and not parsed.scheme) \
                           else url_for('index')
                return redirect(next_url)
            else:
                error = f'Login failed: {reason}' if reason else \
                        'Invalid credentials. Use your Noisebridge wiki username and password.'

    return render_template('login.html', error=error,
                           next=request.args.get('next', ''))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    db.init()
    app.run(host='127.0.0.1', port=8237, debug=True)
