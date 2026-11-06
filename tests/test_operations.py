"""Run real action HTTP requests against a synthetic GitLab service, never live CI."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_backend import backend, response, server
import operations


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        pacing = patch.object(backend, 'REQUEST_INTERVAL', 0)
        pacing.start()
        self.addCleanup(pacing.stop)
        self.permission = True
        self.failure = None
        self.row = dict(id='42', gid='gid://gitlab/Ci::Pipeline/42', iid='7', project='group/project', ref='main')
        with backend.state_session('gitlab.com', 'dummy', self.root) as (state, token, folder, path):
            state.update(snapshot={'pipelines': [self.row]}, stageCache={'gid://gitlab/Ci::Pipeline/42': {}}, nextAttemptAt=time.time() + 60)

    def handler(self, request):
        query = request['query']
        if query in (operations.INFO, operations.JOBS):
            pipeline = dict(id=self.row['gid'], ref='main', source='push', retryable=True,
                            userPermissions={'updatePipeline': self.permission}, mergeRequest=None)
            if query == operations.JOBS:
                pipeline['jobs'] = dict(nodes=[
                    dict(id='gid://gitlab/Ci::Build/9', name='<b>Deploy</b>', status='MANUAL', retryable=False, playable=True, canPlayJob=True, stage={'name': 'Deploy'}),
                    dict(id='gid://gitlab/Ci::Build/10', name='Tests', status='FAILED', retryable=True, playable=False, canPlayJob=True, stage={'name': 'Test'})
                ], pageInfo={'hasNextPage': False})
            return response({'project': {'pipeline': pipeline}})
        if self.failure == 'denied':
            return 403, {'message': 'Insufficient scope'}, {}
        if self.failure == 'timeout':
            return 500, {'message': 'Uncertain upstream outcome'}, {}
        field = next(name for name, mutation in operations.MUTATIONS.values() if mutation == query)
        item = {'id': request['variables'].get('id', 'gid://gitlab/Ci::Pipeline/43'), 'status': 'PENDING'}
        return response({field: {'errors': [], 'job' if field.startswith('job') else 'pipeline': item}})

    def run_action(self, url, operation, job=''):
        return operations.run('gitlab.com', '42', operation, job, token='dummy', cache_root=self.root,
                              client_factory=lambda host, state: backend.Client(host, 'dummy', endpoint=url, state=state))

    def test_job_listing_is_lazy_cached_and_never_mutates(self):
        with server(self.handler) as (url, calls):
            first = self.run_action(url, 'jobs')
            second = self.run_action(url, 'jobs')
        self.assertTrue(first['ok'])
        self.assertEqual(first['jobs'][0]['name'], '<b>Deploy</b>')
        self.assertTrue(first['jobs'][0]['playable'])
        self.assertEqual(first['jobs'], second['jobs'])
        self.assertEqual(len(calls), 1)
        self.assertFalse(any('mutation' in request['query'] for _, request, _ in calls))

    def test_manual_play_and_failed_job_retry_use_exact_selected_job(self):
        with server(self.handler) as (url, calls):
            for operation, job_id in [('play-job', 'gid://gitlab/Ci::Build/9'), ('retry-job', 'gid://gitlab/Ci::Build/10')]:
                result = self.run_action(url, operation, job_id)
                self.assertTrue(result['ok'])
                self.assertEqual(calls[-1][1]['variables'], {'id': job_id})
        self.assertEqual(sum(request['query'].startswith('mutation') for _, request, _ in calls), 2)
        with backend.state_session('gitlab.com', 'dummy', self.root) as (state, *_):
            self.assertEqual(state['nextAttemptAt'], 0)
            self.assertNotIn(self.row['gid'], state['stageCache'])

    def test_pipeline_retry_and_new_run_have_distinct_semantics(self):
        with server(self.handler) as (url, calls):
            self.assertTrue(self.run_action(url, 'retry-pipeline')['ok'])
            self.assertEqual(calls[-1][1]['variables'], {'id': self.row['gid']})
            self.assertTrue(self.run_action(url, 'new-pipeline')['ok'])
            self.assertEqual(calls[-1][1]['variables'], {'project': 'group/project', 'ref': 'main', 'mr': None})

    def test_ineligible_job_and_insufficient_project_role_cannot_write(self):
        with server(self.handler) as (url, calls):
            result = self.run_action(url, 'play-job', 'gid://gitlab/Ci::Build/10')
            self.assertFalse(result['ok'])
            self.permission = False
            result = self.run_action(url, 'new-pipeline')
            self.assertFalse(result['ok'])
        self.assertFalse(any(request['query'].startswith('mutation') for _, request, _ in calls))

    def test_write_scope_denial_explains_api_scope_without_automatic_retry(self):
        self.failure = 'denied'
        with server(self.handler) as (url, calls):
            result = self.run_action(url, 'new-pipeline')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'auth')
        self.assertIn('api scope', result['message'])
        self.assertEqual(sum(request['query'].startswith('mutation') for _, request, _ in calls), 1)

    def test_duplicate_clicks_and_uncertain_writes_are_not_repeated(self):
        for failure in (None, 'timeout'):
            with self.subTest(failure=failure):
                with backend.state_session('gitlab.com', 'dummy', self.root) as (state, *_):
                    state['actions'] = {}
                self.failure = failure
                with server(self.handler) as (url, calls):
                    first = self.run_action(url, 'new-pipeline')
                    second = self.run_action(url, 'new-pipeline')
                self.assertEqual(first, second)
                self.assertEqual(sum(request['query'].startswith('mutation') for _, request, _ in calls), 1)
                if failure:
                    self.assertEqual(first['code'], 'uncertain')

    def test_saved_rate_limit_blocks_reads_and_mutations(self):
        with backend.state_session('gitlab.com', 'dummy', self.root) as (state, *_):
            state['blockedUntil'] = time.time() + 120
        with server(self.handler) as (url, calls):
            result = self.run_action(url, 'new-pipeline')
        self.assertEqual(result['code'], 'rate')
        self.assertEqual(len(calls), 0)


if __name__ == '__main__':
    unittest.main()
