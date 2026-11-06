"""Exercise caching, pagination and rate protection over real local HTTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import formatdate
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_backend import backend, pipeline, response, server


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.pacing = patch.object(backend, 'REQUEST_INTERVAL', 0)
        self.pacing.start()
        self.addCleanup(self.pacing.stop)
        self.mode = 'ok'
        self.headers = {}
        self.running = False

    def handler(self, request):
        if self.mode == 'rate':
            return 429, {'message': 'private upstream diagnostic'}, self.headers
        variables = request['variables']
        if request['query'] == backend.PROJECTS:
            return response({'projects': {'nodes': [{'fullPath': 'shop/one'}], 'pageInfo': {'hasNextPage': False}}})
        if request['query'] == backend.PIPELINES:
            offset = int(variables.get('after') or 0)
            rows = []
            for ident in range(100 - offset, max(0, 80 - offset), -1):
                row = pipeline(ident, datetime.fromtimestamp(1700000000 + ident, timezone.utc).isoformat(),
                               'RUNNING' if self.running and ident == 100 else 'SUCCESS')
                row['updatedAt'] = row['createdAt']
                rows.append(row)
            return response({'project': {'pipelines': {'nodes': rows, 'pageInfo': {'hasNextPage': offset + 20 < 100, 'endCursor': str(offset + 20)}}}})
        project = {'pipeline': {'stages': {'nodes': [{'name': '<b>Build</b>', 'status': 'success'}], 'pageInfo': {'hasNextPage': False}}}}
        return response({'project': project} if 'iid' in variables else {'p' + str(i): project for i in range(len(variables) // 2)})

    def fetch(self, url, count=20, token='dummy-private-token'):
        return backend.cached_inventory('gitlab.com', count, token=token, cache_root=self.root,
                                        client_factory=lambda host, state: backend.Client(host, token, endpoint=url, state=state))

    def edit_state(self, update):
        path = next(self.root.glob('*.json'))
        state = json.loads(path.read_text())
        update(state)
        backend.write_state(path, state)

    def expire_refresh(self):
        self.edit_state(lambda state: state.update(nextAttemptAt=0, blockedUntil=0, cooldownUntil=0, requestTimes=[]))

    def test_twenty_default_and_older_pages_only_on_load_more(self):
        with server(self.handler) as (url, calls):
            first = self.fetch(url)
            self.assertEqual(len(first['pipelines']), 20)
            self.assertEqual(first['requests'], 5)  # Discovery + one page + three stage batches.
            self.assertTrue(first['canLoadMore'])
            self.assertEqual(self.fetch(url)['requests'], 0)
            before = len(calls)
            more = self.fetch(url, 40)
            self.assertEqual([row['id'] for row in more['pipelines']], [str(i) for i in range(100, 60, -1)])
            new_calls = [request for _, request, _ in calls[before:]]
            self.assertEqual(sum(request['query'] == backend.PROJECTS for request in new_calls), 0)
            self.assertEqual(sum(request['query'] == backend.PIPELINES for request in new_calls), 1)
            page = next(request for request in new_calls if request['query'] == backend.PIPELINES)
            self.assertEqual(page['variables']['after'], '20')
            self.assertEqual(more['requests'], 4)
            self.assertEqual(self.fetch(url, 40)['requests'], 0)
            smaller = self.fetch(url, 20)
            self.assertEqual(len(smaller['pipelines']), 20)
            self.assertTrue(smaller['canLoadMore'])
            self.assertEqual(smaller['requests'], 0)
            # All loaded data is private; the authentication token never reaches the cache.
            path = next(self.root.glob('*.json'))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('dummy-private-token', path.read_text())

    def test_completed_stages_and_projects_are_reused_on_next_refresh(self):
        with server(self.handler) as (url, calls):
            self.fetch(url)
            self.expire_refresh()
            before = len(calls)
            result = self.fetch(url)
            self.assertEqual(result['requests'], 1)  # Only the project's latest pipeline page.
            self.assertEqual(calls[before][1]['query'], backend.PIPELINES)
            self.assertTrue(all(row['stagesKnown'] for row in result['pipelines']))

    def test_cross_project_pagination_keeps_global_order_and_only_pages_busy_project(self):
        def handler(request):
            if request['query'] == backend.PROJECTS:
                return response({'projects': {'nodes': [{'fullPath': 'shop/one'}, {'fullPath': 'shop/two'}], 'pageInfo': {'hasNextPage': False}}})
            if request['query'] == backend.PIPELINES and request['variables']['path'] == 'shop/two':
                copied = dict(request, variables=dict(request['variables'], after=str(int(request['variables'].get('after') or 0) + 40)))
                return self.handler(copied)
            return self.handler(request)
        with server(handler) as (url, calls):
            first = self.fetch(url)
            self.assertTrue(all(row['project'] == 'shop/one' for row in first['pipelines']))
            before = len(calls)
            more = self.fetch(url, 40)
            self.assertEqual([row['id'] for row in more['pipelines']], [str(i) for i in range(100, 60, -1)])
            self.assertTrue(all(row['project'] == 'shop/one' for row in more['pipelines']))
            page_calls = [request for _, request, _ in calls[before:] if request['query'] == backend.PIPELINES]
            self.assertEqual(len(page_calls), 1)
            self.assertEqual(page_calls[0]['variables']['path'], 'shop/one')

    def test_running_stages_refresh_but_completed_stages_do_not(self):
        self.running = True
        with server(self.handler) as (url, calls):
            self.fetch(url)
            self.expire_refresh()
            self.edit_state(lambda state: [item.update(fetchedAt=time.time() - 61) for item in state['stageCache'].values()])
            before = len(calls)
            result = self.fetch(url)
            self.assertEqual(result['requests'], 2)
            requests = [request for _, request, _ in calls[before:]]
            self.assertEqual(requests[-1]['variables']['iid'], '100')

    def test_two_simultaneous_monitor_processes_share_one_refresh(self):
        barrier = threading.Barrier(2)
        with server(self.handler) as (url, calls), ThreadPoolExecutor(max_workers=2) as pool:
            def run():
                barrier.wait(timeout=2)
                return self.fetch(url)
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(result['requests'] for result in results), [0, 5])
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(len(result['pipelines']) == 20 for result in results))

    def test_429_stops_immediately_and_survives_reopen_load_more_and_new_client(self):
        with server(self.handler) as (url, calls):
            original = self.fetch(url)
            self.expire_refresh()
            self.mode = 'rate'
            self.headers = {'Retry-After': '120'}
            before = len(calls)
            result = self.fetch(url)
            self.assertEqual(len(calls) - before, 1)
            self.assertEqual(result['code'], 'rate')
            self.assertGreater(result['retryAt'], (time.time() + 118) * 1000)
            self.assertEqual(result['pipelines'], original['pipelines'])
            for count in (20, 40, 20):
                cached = self.fetch(url, count)
                self.assertEqual(cached['requests'], 0)
                self.assertEqual(cached['code'], 'rate')
                self.assertEqual(cached['pipelines'], original['pipelines'])
            self.assertEqual(len(calls) - before, 1)

    def test_quota_reserve_pauses_before_a_429(self):
        state = {}
        with server(lambda _: (200, {'data': {'currentUser': None}}, {'RateLimit-Remaining': '5', 'RateLimit-Reset': str(int(time.time()) + 120)})) as (url, calls):
            client = backend.Client('gitlab.com', 'dummy', endpoint=url, state=state)
            client.query('query { currentUser { id } }', {})
            with self.assertRaises(backend.ClientError) as caught:
                client.query('query { currentUser { id } }', {})
            self.assertEqual(caught.exception.code, 'rate')
            self.assertEqual(len(calls), 1)

    def test_429_without_snapshot_or_headers_does_not_repeat_on_reopen(self):
        self.mode = 'rate'
        with server(self.handler) as (url, calls):
            first = self.fetch(url)
            second = self.fetch(url)
        self.assertFalse(first['ok'])
        self.assertEqual(first['code'], 'rate')
        self.assertGreater(first['retryAt'], time.time() * 1000)
        self.assertFalse(second['ok'])
        self.assertEqual(second['requests'], 0)
        self.assertEqual(len(calls), 1)

    def test_rolling_local_budget_prevents_network_request(self):
        state = {'requestTimes': [time.time() - 1] * backend.REQUEST_BUDGET}
        with server(self.handler) as (url, calls):
            client = backend.Client('gitlab.com', 'dummy', endpoint=url, state=state)
            with self.assertRaises(backend.ClientError) as caught:
                client.query(backend.PROJECTS, {})
            self.assertEqual(caught.exception.code, 'budget')
            self.assertEqual(len(calls), 0)

    def test_credential_change_does_not_reuse_old_account_snapshot(self):
        with server(self.handler) as (url, _):
            self.fetch(url)
            other = self.fetch(url, token='a-different-token')
        self.assertEqual(other['requests'], 5)
        self.assertEqual(len(list(self.root.glob('*.json'))), 2)

    def test_retry_after_dates_and_missing_headers(self):
        now = time.time()
        self.assertGreaterEqual(backend.reset_time({'Retry-After': formatdate(now + 180, usegmt=True)}, now), now + 179)
        self.assertEqual(backend.reset_time({}, now), 0)


if __name__ == '__main__':
    unittest.main()
