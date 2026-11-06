"""Run the production HTTP client against a local synthetic GitLab server."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend


@contextmanager
def server(responder):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, request, self.headers.get('Authorization')))
            status, body, headers = responder(request)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if body is not None:
                self.wfile.write(json.dumps(body).encode())

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(httpd.server_port) + '/api/graphql', calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def response(data):
    return 200, {'data': data}, {}


def pipeline(ident, created, status='SUCCESS'):
    return dict(id='gid://gitlab/Ci::Pipeline/' + str(ident), iid=str(ident), status=status,
                createdAt=created, startedAt=created, finishedAt=created, duration=32,
                ref='stage', sha='a' * 40, commitTitle='<img src="https://example.invalid/tracker">',
                user={'name': 'A <b>user</b>', 'username': 'alex'})


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        pacing = patch.object(backend, 'REQUEST_INTERVAL', 0)
        pacing.start()
        self.addCleanup(pacing.stop)

    def test_paginated_projects_global_order_and_partial_access(self):
        def handler(request):
            query, variables = request['query'], request['variables']
            if query == backend.PROJECTS:
                if variables['after'] is None:
                    return response({'projects': {'nodes': [{'fullPath': 'shop/one'}],
                                                  'pageInfo': {'hasNextPage': True, 'endCursor': 'page2'}}})
                self.assertEqual(variables['after'], 'page2')
                return response({'projects': {'nodes': [{'fullPath': 'shop/two'}, {'fullPath': 'private/denied'}, {'fullPath': 'shop/one'}],
                                              'pageInfo': {'hasNextPage': False, 'endCursor': 'done'}}})
            if query == backend.PIPELINES:
                if variables['path'] == 'private/denied':
                    return 403, {'message': 'secret server diagnostic'}, {}
                nodes = [pipeline(10, '2026-11-01T12:00:00Z'), pipeline(9, '2026-11-01T11:00:00Z')]
                if variables['path'] == 'shop/two':
                    nodes = [pipeline(11, '2026-11-01T12:01:00Z', 'RUNNING')]
                return response({'project': {'pipelines': {'nodes': nodes}}})
            if variables.get('iid') == '9':
                return 429, {'message': 'Too many requests'}, {}
            project = {'pipeline': {'stages': {'nodes': [{'name': '<b>Build</b>', 'status': 'success'}, {'name': 'Deploy', 'status': 'manual'}],
                                                'pageInfo': {'hasNextPage': False}}}}
            return response({'project': project} if 'iid' in variables else {'p' + str(i): project for i in range(len(variables) // 2)})
        with server(handler) as (url, calls):
            result = backend.inventory(backend.Client('gitlab.com', 'test-token', endpoint=url), 2)
        self.assertEqual([row['id'] for row in result['pipelines']], ['11', '10'])
        self.assertEqual(result['projects'], 3)
        self.assertEqual(result['failedProjects'], 1)
        self.assertEqual(result['failedStages'], 0)
        self.assertTrue(result['pipelines'][0]['stagesKnown'])
        self.assertEqual(result['pipelines'][0]['stages'][0]['name'], '<b>Build</b>')
        self.assertTrue(result['pipelines'][0]['title'].startswith('<img'))
        self.assertTrue(all(path == '/api/graphql' and auth == 'Bearer test-token' for path, _, auth in calls))
        self.assertTrue(all('mutation' not in request['query'] for _, request, _ in calls))
        self.assertEqual(sum('StagesBatch' in request['query'] for _, request, _ in calls), 1)

    def test_stage_failure_is_unknown_not_passed(self):
        def handler(request):
            if request['query'] == backend.PROJECTS:
                return response({'projects': {'nodes': [{'fullPath': 'shop/one'}], 'pageInfo': {'hasNextPage': False}}})
            if request['query'] == backend.PIPELINES:
                return response({'project': {'pipelines': {'nodes': [pipeline(10, '2026-11-01T12:00:00Z')]}}})
            return 500, {'message': 'failure'}, {}
        with server(handler) as (url, _):
            result = backend.inventory(backend.Client('gitlab.com', 'dummy', endpoint=url))
        self.assertEqual(result['failedStages'], 1)
        self.assertFalse(result['pipelines'][0]['stagesKnown'])

    def test_errors_and_redirects_do_not_leak_token_or_server_body(self):
        for status, expected in [(401, 'auth'), (403, 'auth'), (429, 'rate'), (500, 'network'), (302, 'network')]:
            with self.subTest(status=status):
                with server(lambda _: (status, {'message': 'secret-token'}, {'Location': 'https://example.invalid/steal'})) as (url, calls):
                    with self.assertRaises(backend.ClientError) as caught:
                        backend.Client('gitlab.com', 'secret-token', endpoint=url).query(backend.PROJECTS, {})
                    self.assertEqual(caught.exception.code, expected)
                    self.assertNotIn('secret-token', str(caught.exception))
                    self.assertEqual(len(calls), 1)

    def test_graphql_error_is_not_an_empty_success(self):
        with server(lambda _: (200, {'errors': [{'message': 'secret diagnostic'}], 'data': {'projects': None}}, {})) as (url, _):
            with self.assertRaises(backend.ClientError) as caught:
                backend.Client('gitlab.com', 'dummy', endpoint=url).query(backend.PROJECTS, {})
        self.assertEqual(caught.exception.code, 'response')
        self.assertNotIn('secret diagnostic', str(caught.exception))

    def test_timeout_budget(self):
        client = backend.Client('gitlab.com', 'dummy')
        client.deadline = 0
        with self.assertRaises(backend.ClientError) as caught:
            client.query(backend.PROJECTS, {})
        self.assertEqual(caught.exception.code, 'timeout')

    def test_token_permissions_and_host_scoping(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_CONFIG_HOME': folder, 'GITLAB_TOKEN': 'dummy', 'GITLAB_HOST': 'gitlab.com'}):
            self.assertIsNone(backend.read_token('gitlab.example.org'))
            self.assertEqual(backend.read_token('gitlab.com'), 'dummy')
            path = backend.token_path('gitlab.com')
            path.parent.mkdir(parents=True)
            path.write_text('file-token')
            path.chmod(0o644)
            with self.assertRaises(backend.ClientError):
                backend.read_token('gitlab.com')
            path.chmod(0o600)
            self.assertEqual(backend.read_token('gitlab.com'), 'file-token')
            alias = backend.token_path('other.example.org')
            alias.symlink_to(path)
            with self.assertRaises(backend.ClientError):
                backend.read_token('other.example.org')

    def test_hostname_rejects_paths_options_and_credentials(self):
        for value in ['https://gitlab.com', '-example.com', 'user@gitlab.com', 'gitlab.com/path', 'gitlab.com\n']:
            with self.subTest(value=value), self.assertRaises(backend.ClientError):
                backend.host_name(value)


if __name__ == '__main__':
    unittest.main()
