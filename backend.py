#!/usr/bin/env python3
"""GitLab pipeline client and shared cache. Python standard library; glab is optional."""
import argparse
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import sys
import urllib.error
import urllib.request

if __name__ == '__main__':
    sys.modules['backend'] = sys.modules[__name__]

MAX_BYTES = 4 * 1024 * 1024
MIN_REFRESH = 60
PROJECT_TTL = 1800
STAGE_TTL = 3600
REQUEST_INTERVAL = 1.0
REQUEST_BUDGET = 30  # Rolling minute, shared by every process using this credential.
STAGE_BATCH = 8
PROJECTS = '''query Projects($after: String) {
  projects(membership: true, first: 100, after: $after) {
    nodes { fullPath }
    pageInfo { hasNextPage endCursor }
  }
}'''
PIPELINES = '''query Pipelines($path: ID!, $count: Int!, $after: String) {
  project(fullPath: $path) {
    pipelines(first: $count, after: $after) {
      nodes {
        id iid status createdAt updatedAt startedAt finishedAt duration ref refPath sha commitTitle
        retryable userPermissions { updatePipeline }
        user { name username avatarUrl }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}'''
STAGES = '''query Stages($path: ID!, $iid: ID!) {
  project(fullPath: $path) {
    pipeline(iid: $iid) {
      stages(first: 16) { nodes { name status } pageInfo { hasNextPage } }
    }
  }
}'''


class ClientError(Exception):
    def __init__(self, code, message, retry_at=0):
        self.code = code
        self.retry_at = retry_at
        super().__init__(message)


def reset_time(headers, now):
    """Honor server timing, including date-form Retry-After and relative clock skew."""
    deadlines = []
    try:
        server_now = parsedate_to_datetime(headers.get('Date', '')).timestamp()
    except (ValueError, TypeError):
        server_now = now
    retry = headers.get('Retry-After')
    if retry:
        try:
            deadlines.append(now + max(0, float(retry)))
        except ValueError:
            try:
                deadlines.append(now + max(0, parsedate_to_datetime(retry).timestamp() - server_now))
            except (TypeError, ValueError):
                pass
    try:
        reset = float(headers.get('RateLimit-Reset') or headers.get('X-RateLimit-Reset') or 0)
        if reset:
            deadlines.append(now + max(0, reset - server_now))
    except ValueError:
        pass
    return max(deadlines, default=0)


def host_name(value):
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?(?::[0-9]{1,5})?', value):
        raise ClientError('config', 'Enter a GitLab hostname, for example gitlab.com.')
    return value.lower()


def token_path(host):
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'omarchy/pipelines' / (host.replace(':', '_') + '.token')


def read_token(host):
    path = token_path(host)
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ClientError('auth', 'The saved token must be a regular file owned by you with mode 600.')
        with path.open() as stream:
            value = stream.read(4097).strip()
        if len(value) > 4096 or any(c.isspace() for c in value):
            raise ClientError('auth', 'The saved GitLab token is invalid. Connect again.')
        if value:
            return value
    # Scope environment credentials to their declared host (GitLab.com by default).
    if host == os.environ.get('GITLAB_HOST', 'gitlab.com').lower():
        return os.environ.get('GITLAB_TOKEN') or os.environ.get('GITLAB_ACCESS_TOKEN')
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential to another origin, even on a redirect.
        return None


class Client:
    def __init__(self, host, token=None, endpoint=None, state=None):
        self.host = host_name(host)
        self.token = token if token is not None else read_token(self.host)
        self.endpoint = endpoint or 'https://' + self.host + '/api/graphql'
        self.deadline = time.monotonic() + 110
        self.state = state if state is not None else {}
        self.requests = 0
        if not self.token and not shutil.which('glab'):
            raise ClientError('auth', 'Connect GitLab to see your pipelines.')

    def reserve_request(self):
        now = time.time()
        blocked = self.state.get('blockedUntil', 0)
        if blocked > now:
            raise ClientError('rate', 'GitLab refresh is paused until its rate-limit window resets. Cached pipelines remain available.', blocked)
        times = [stamp for stamp in self.state.get('requestTimes', []) if now - 60 < stamp <= now]
        self.state['requestTimes'] = times
        if len(times) >= REQUEST_BUDGET:
            until = times[0] + 62
            raise ClientError('budget', 'Refresh paused to stay within the plugin request budget. Cached pipelines remain available.', until)
        delay = max(0, (times[-1] + REQUEST_INTERVAL - now) if times else 0)
        timeout = min(18, self.deadline - time.monotonic())
        if timeout <= delay:
            raise ClientError('timeout', 'GitLab took too long to respond. Try refreshing.')
        if delay:
            time.sleep(delay)
        self.state['requestTimes'].append(time.time())
        self.requests += 1
        return timeout

    def query(self, query, variables):
        timeout = self.reserve_request()
        payload = json.dumps(dict(query=query, variables=variables)).encode()
        try:
            if self.token:
                request = urllib.request.Request(self.endpoint, data=payload, headers={
                    'Authorization': 'Bearer ' + self.token,
                    'Content-Type': 'application/json', 'User-Agent': 'omarchy-gitlab-pipelines/1.0'})
                with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
                    raw = response.read(MAX_BYTES + 1)
                    self.observe_headers(response.headers)
            else:
                # Delegate credential/keyring handling to glab, without extracting its token.
                with tempfile.TemporaryFile() as output:
                    result = subprocess.run(['glab', 'api', '--hostname', self.host, '--method', 'POST',
                                             'graphql', '--include', '--input', '-'], input=payload,
                                            stdout=output, stderr=subprocess.DEVNULL, timeout=timeout,
                                            env=dict(os.environ, GLAB_CHECK_UPDATE='false'))
                    output.seek(0)
                    raw = output.read(MAX_BYTES + 1)
                    # --include provides status and quota headers without exposing credentials.
                    separator = b'\r\n\r\n' if b'\r\n\r\n' in raw else b'\n\n'
                    if raw.startswith(b'HTTP/') and separator in raw:
                        head, raw = raw.split(separator, 1)
                        lines = head.decode('latin1').splitlines()
                        status = int(lines[0].split()[1])
                        from email.parser import Parser
                        headers = Parser().parsestr('\n'.join(lines[1:]))
                        if status >= 400:
                            raise urllib.error.HTTPError(self.endpoint, status, '', headers, None)
                        self.observe_headers(headers)
                    elif result.returncode:
                        raise ClientError('auth', 'GitLab CLI could not fetch pipelines. Check glab auth login and your connection.')
            if len(raw) > MAX_BYTES:
                raise ClientError('response', 'GitLab returned too much data.')
            body = json.loads(raw)
        except urllib.error.HTTPError as error:
            code = 'auth' if error.code in (401, 403) else 'rate' if error.code == 429 else 'network'
            messages = {'auth': ('GitLab rejected the action. Connect again with api scope to run pipelines and jobs.' if query.lstrip().startswith('mutation') else 'GitLab rejected the credentials. Connect again with read_api permission.'),
                        'rate': 'GitLab rate limit reached. Wait a few minutes before refreshing.',
                        'network': 'GitLab returned HTTP ' + str(error.code) + '. Try again later.'}
            retry_at = 0
            if code == 'rate':
                streak = min(6, int(self.state.get('rateStreak', 0)) + 1)
                self.state['rateStreak'] = streak
                retry_at = max(time.time() + 60, reset_time(error.headers, time.time()) or time.time() + min(3600, 60 * 2 ** (streak - 1))) + 5
                self.state['blockedUntil'] = retry_at
            error.close()
            raise ClientError(code, messages[code], retry_at) from None
        except (TimeoutError, subprocess.TimeoutExpired):
            raise ClientError('timeout', 'GitLab took too long to respond. Try refreshing.') from None
        except (urllib.error.URLError, OSError):
            raise ClientError('network', 'Could not reach GitLab. Check your connection.') from None
        except (ValueError, UnicodeError):
            raise ClientError('response', 'GitLab returned an invalid response.') from None
        if not isinstance(body, dict) or body.get('errors') or not isinstance(body.get('data'), dict):
            # Server diagnostics may contain sensitive data. Do not send them to QML/logs.
            if query.lstrip().startswith('mutation'):
                raise ClientError('permission', 'GitLab rejected the action. Reconnect with api scope and check your project permissions.')
            raise ClientError('response', 'GitLab could not complete the pipeline query. Check your access and GitLab version.')
        return body['data']

    def observe_headers(self, headers):
        self.state['rateStreak'] = 0
        remaining = headers.get('RateLimit-Remaining') or headers.get('X-RateLimit-Remaining')
        try:
            if remaining is not None and int(remaining) <= 5:
                self.state['blockedUntil'] = max(time.time() + 60, reset_time(headers, time.time())) + 5
        except ValueError:
            pass


def text(value, limit=512):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))[:limit]


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000
    except (AttributeError, ValueError, OverflowError):
        return 0


def normalize(project, item):
    ident = str(item.get('id', '')).rsplit('/', 1)[-1]
    if not ident.isdigit():
        raise ClientError('response', 'GitLab returned an invalid pipeline ID.')
    user = item.get('user') or {}
    status = text(item.get('status'), 32).lower()
    return dict(id=ident, gid=item['id'], iid=text(item.get('iid'), 32), project=text(project),
                status=status, updatedAt=timestamp(item.get('updatedAt')), title=text(item.get('commitTitle')), ref=text(item.get('ref'), 256),
                sha=text(item.get('sha'), 64), isTag='/-/tags/' in str(item.get('refPath') or ''), author=text(user.get('name'), 100),
                username=text(user.get('username'), 100), avatarUrl=text(user.get('avatarUrl'), 2048), avatar='',
                retryable=bool(item.get('retryable')), canUpdate=bool((item.get('userPermissions') or {}).get('updatePipeline')), createdAt=timestamp(item.get('createdAt')),
                startedAt=timestamp(item.get('startedAt')), finishedAt=timestamp(item.get('finishedAt')),
                duration=max(0, min(31536000, float(item.get('duration') or 0))), stages=[], stagesKnown=False)


def stage_batch(rows):
    if len(rows) == 1:
        row = rows[0]
        return STAGES, {'path': row['project'], 'iid': row['iid']}
    declarations, fields, variables = [], [], {}
    for index, row in enumerate(rows):
        path, iid = 'path' + str(index), 'iid' + str(index)
        declarations.extend(['$' + path + ': ID!', '$' + iid + ': ID!'])
        variables.update({path: row['project'], iid: row['iid']})
        fields.append('p' + str(index) + ': project(fullPath: $' + path + ') { pipeline(iid: $' + iid + ') { '
                      'stages(first: 16) { nodes { name status } pageInfo { hasNextPage } } } }')
    return 'query StagesBatch(' + ', '.join(declarations) + ') { ' + ' '.join(fields) + ' }', variables


def inventory(client, count=20, reuse_pages=False):
    state = client.state
    now = time.time()
    projects = state.get('projectPaths')
    if projects is None or now - state.get('projectsFetchedAt', 0) >= PROJECT_TTL:
        projects, cursor, seen = [], None, set()
        for _ in range(20):
            connection = client.query(PROJECTS, {'after': cursor})['projects']
            for node in connection['nodes']:
                path = node.get('fullPath') if isinstance(node, dict) else None
                if isinstance(path, str) and 0 < len(path) <= 512 and path not in seen:
                    projects.append(path)
                    seen.add(path)
            page = connection['pageInfo']
            if not page['hasNextPage']:
                break
            next_cursor = page.get('endCursor')
            if not next_cursor or next_cursor == cursor:
                raise ClientError('response', 'GitLab project pagination did not advance.')
            cursor = next_cursor
        else:
            raise ClientError('limit', 'More than 2,000 member projects. Narrow the account access before using this overview.')
        state.update(projectPaths=projects, projectsFetchedAt=now)

    project_rows = state.setdefault('projectRows', {})
    pages = state.setdefault('pipelinePages', {})
    refreshed, first_error, halt = set(), None, None

    def fetch_page(path, after=None):
        project = client.query(PIPELINES, {'path': path, 'count': 20, 'after': after}).get('project')
        if project is None:
            raise ClientError('access', 'A project is no longer accessible.')
        connection = project.get('pipelines') or {}
        rows = [normalize(path, item) for item in connection.get('nodes', [])[:20] if item]
        info = connection.get('pageInfo') or {'hasNextPage': False}
        if info['hasNextPage'] and (not info.get('endCursor') or info.get('endCursor') == after):
            raise ClientError('response', 'GitLab pipeline pagination did not advance.')
        if after is None:
            project_rows[path] = rows
        else:
            existing = {row['gid'] for row in project_rows.get(path, [])}
            project_rows.setdefault(path, []).extend(row for row in rows if row['gid'] not in existing)
        pages[path] = dict(hasNextPage=info['hasNextPage'], endCursor=info.get('endCursor'))

    def merged():
        rows = [row for path in projects for row in project_rows.get(path, [])]
        rows.sort(key=lambda row: (row['createdAt'], int(row['id'])), reverse=True)
        return rows

    # Reopening or loading an older page reuses the project scans from this minute.
    start = int(state.get('projectCursor', 0)) % max(1, len(projects))
    ordered = projects[start:] + projects[:start]
    for path in ordered:
        if reuse_pages and path in project_rows and path in pages:
            refreshed.add(path)
            continue
        try:
            fetch_page(path)
            refreshed.add(path)
        except ClientError as error:
            first_error = first_error or error
            if error.code in ('rate', 'budget', 'timeout', 'network'):
                halt = error
                state['projectCursor'] = projects.index(path)
                break
    else:
        state['projectCursor'] = 0
    # Only page a project if its unseen runs could belong in the requested global window.
    while not halt:
        candidates = merged()
        cutoff = (candidates[count - 1]['createdAt'], int(candidates[count - 1]['id'])) if len(candidates) >= count else (0, 0)
        paths = [path for path in projects if pages.get(path, {}).get('hasNextPage') and
                 (not project_rows.get(path) or len(candidates) < count or (project_rows[path][-1]['createdAt'], int(project_rows[path][-1]['id'])) > cutoff)]
        if not paths:
            break
        path = max(paths, key=lambda value: (project_rows.get(value) or [{}])[-1].get('createdAt', 0))
        try:
            fetch_page(path, pages[path]['endCursor'])
        except ClientError as error:
            first_error = first_error or error
            halt = error
    candidates = merged()
    rows = candidates[:count]
    has_more = len(candidates) > count or any(page.get('hasNextPage') for path, page in pages.items() if path in projects)
    if projects and not rows and not refreshed and first_error:
        raise halt or first_error

    stage_cache = state.setdefault('stageCache', {})
    needs_stages = []
    final_states = {'success', 'failed', 'canceled', 'skipped'}
    for row in rows:
        saved = stage_cache.get(row['gid'], {})
        fingerprint = [row['status'], row.get('updatedAt', 0)]
        if saved:
            row.update(stages=saved.get('stages', []), stagesKnown=True, stagesMore=saved.get('stagesMore', False))
        if not (saved and saved.get('fingerprint') == fingerprint
                and now - saved.get('fetchedAt', 0) < (STAGE_TTL if row['status'] in final_states else MIN_REFRESH)):
            needs_stages.append(row)

    stage_failures = 0
    for start in range(0, len(needs_stages), STAGE_BATCH):
        if halt:
            stage_failures += len(needs_stages) - start
            break
        batch = needs_stages[start:start + STAGE_BATCH]
        try:
            query, variables = stage_batch(batch)
            result = client.query(query, variables)
            for index, row in enumerate(batch):
                project = result.get('project' if len(batch) == 1 else 'p' + str(index))
                data = project.get('pipeline') if project else None
                if data is None or not data.get('stages'):
                    stage_failures += 1
                    continue
                connection = data['stages']
                row['stages'] = [dict(name=text(stage.get('name'), 100), status=text(stage.get('status'), 32).lower())
                             for stage in connection['nodes'][:16] if stage]
                row['stagesMore'] = bool(connection['pageInfo']['hasNextPage'])
                row['stagesKnown'] = True
                stage_cache[row['gid']] = dict(stages=row['stages'], stagesMore=row['stagesMore'],
                                              fingerprint=[row['status'], row.get('updatedAt', 0)], fetchedAt=time.time())
        except ClientError as error:
            stage_failures += len(batch)
            first_error = first_error or error
            if error.code in ('rate', 'budget', 'timeout', 'network'):
                halt = error
    # Preserve page cursors and loaded history for the next Load more action.
    state['projectRows'] = {path: project_rows.get(path, []) for path in projects}
    state['stageCache'] = dict(sorted(stage_cache.items(), key=lambda entry: entry[1]['fetchedAt'], reverse=True)[:500])
    result = dict(ok=True, pipelines=rows, projects=len(projects), failedProjects=len(projects) - len(refreshed),
                  failedStages=stage_failures, canLoadMore=has_more and count < 10000, limit=count,
                  fetchedAt=time.time() * 1000 if refreshed else state.get('snapshot', {}).get('fetchedAt', time.time() * 1000))
    if halt:
        result.update(code=halt.code, message=str(halt), retryAt=halt.retry_at * 1000)
    return result


def cache_identity(host, token):
    if token:
        return hashlib.sha256(token.encode()).hexdigest()[:24]
    # glab owns its credential store. A config change invalidates its snapshot namespace.
    config = Path(os.environ.get('GLAB_CONFIG_DIR', str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'glab-cli'))) / 'config.yml'
    try:
        stamp = str(config.stat().st_mtime_ns)
    except OSError:
        stamp = 'missing'
    return hashlib.sha256(('glab:' + host + ':' + str(config) + ':' + stamp).encode()).hexdigest()[:24]


def read_state(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
                return {}
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            return {}
        state = json.loads(raw)
        return state if isinstance(state, dict) and state.get('version') == 1 else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def write_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_session(host, token=None, cache_root=None):
    host = host_name(host)
    token = token if token is not None else read_token(host)
    directory = Path(cache_root) if cache_root else Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'omarchy/pipelines'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ClientError('config', 'Pipeline cache directory must be owned by you and must not be a symlink.')
    directory.chmod(0o700)
    prefix = host.replace(':', '_') + '-' + cache_identity(host, token)
    path = directory / (prefix + '.json')
    fd = os.open(directory / (prefix + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode) or os.fstat(lock.fileno()).st_uid != os.getuid():
            raise ClientError('config', 'Pipeline cache lock must be owned by you.')
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state(path)
        state['version'] = 1
        try:
            yield state, token, directory, path
        finally:
            write_state(path, state)


def cached_inventory(host, count=20, *, token=None, client_factory=None, cache_root=None):
    with state_session(host, token, cache_root) as (state, token, directory, path):
        if state.get('viewSchema') != 2:
            state['nextAttemptAt'] = 0
            state['viewSchema'] = 2
        now = time.time()
        snapshot = state.get('snapshot')
        more = bool(snapshot and count > state.get('count', 0))
        retry_at = max(state.get('blockedUntil', 0), state.get('nextAttemptAt', 0))
        def cached_result():
            result = dict(snapshot, cached=True, nextRefreshAt=retry_at * 1000, requests=0)
            if len(result.get('pipelines', [])) > count:
                result.update(pipelines=result['pipelines'][:count], canLoadMore=True, limit=count)
            return result
        cooldown = max(state.get('blockedUntil', 0), state.get('cooldownUntil', 0))
        if snapshot and (now < cooldown or (now < retry_at and not more)):
            return cached_result()
        if now < cooldown or (now < retry_at and not more):
            return dict(ok=False, code=state.get('code', 'rate'), message=state.get('message', 'GitLab refresh is paused. Please wait.'),
                        retryAt=retry_at * 1000, nextRefreshAt=retry_at * 1000, requests=0)
        client = client_factory(host, state) if client_factory else Client(host, token=token, state=state)
        try:
            result = inventory(client, count, reuse_pages=more and now < retry_at)
            from avatars import hydrate_avatars
            hydrate_avatars(client, result['pipelines'], directory)
            state['snapshot'] = result
            state['count'] = count
            state['cooldownUntil'] = max(result.get('retryAt', 0) / 1000, state.get('blockedUntil', 0)) if result.get('code') else 0
            state['nextAttemptAt'] = max(time.time() + MIN_REFRESH, result.get('retryAt', 0) / 1000, state.get('blockedUntil', 0))
        except ClientError as error:
            state.update(code=error.code, message=str(error))
            state['cooldownUntil'] = max(time.time() + MIN_REFRESH, error.retry_at, state.get('blockedUntil', 0))
            state['nextAttemptAt'] = max(time.time() + MIN_REFRESH, error.retry_at, state.get('blockedUntil', 0))
            result = dict(snapshot or {'ok': False}, code=error.code, message=str(error), retryAt=state['nextAttemptAt'] * 1000)
            # Preserve a usable snapshot with the current cooldown notice for other processes.
            if snapshot:
                state['snapshot'] = result
        result.update(nextRefreshAt=state['nextAttemptAt'] * 1000, requests=client.requests, cached=False)
        return result


def login(host):
    print('Create a personal access token with api permission to run/retry pipelines and jobs. read_api only enables viewing:')
    print('https://' + host + '/-/user_settings/personal_access_tokens')
    print('The token is stored locally with mode 600. Input is hidden.')
    token = getpass.getpass('GitLab token: ').strip()
    if not token or len(token) > 4096 or any(c.isspace() for c in token):
        raise ClientError('auth', 'No valid token entered.')
    Client(host, token).query('query { currentUser { username } }', {})
    path = token_path(host)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.token-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Connected. Open Pipelines and refresh.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['list', 'login'], nargs='?', default='list')
    parser.add_argument('--host', default='gitlab.com', type=host_name)
    parser.add_argument('--count', type=int, choices=range(10, 10001), default=20)
    args = parser.parse_args()
    try:
        if args.action == 'login':
            login(args.host)
        else:
            print(json.dumps(cached_inventory(args.host, args.count)))
    except ClientError as error:
        if args.action == 'login':
            parser.exit(1, str(error) + '\n')
        print(json.dumps(dict(ok=False, code=error.code, message=str(error))))
    except (KeyError, TypeError, ValueError, AttributeError):
        print(json.dumps(dict(ok=False, code='response', message='GitLab returned an unexpected response.')))
    except OSError:
        print(json.dumps(dict(ok=False, code='config', message='Could not access the private pipeline cache. Check your cache directory permissions.')))


if __name__ == '__main__':
    main()
