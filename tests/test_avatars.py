import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from test_backend import backend
from avatars import avatar_url, hydrate_avatars, image_extension


def png():
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x60\x90\xc0')) + chunk(b'IEND', b'')


class AvatarTests(unittest.TestCase):
    def test_source_allowlist_and_svg_rejection(self):
        self.assertEqual(avatar_url('gitlab.com', 'https://evil.invalid/avatar.png'), '')
        self.assertEqual(avatar_url('gitlab.com', 'http://gitlab.com/avatar.png'), '')
        self.assertEqual(avatar_url('gitlab.com', '//evil.invalid/avatar.png'), '')
        self.assertEqual(avatar_url('gitlab.com', '/uploads/avatar.png'), 'https://gitlab.com/uploads/avatar.png')
        self.assertEqual(avatar_url('gitlab.com', 'https://secure.gravatar.com/avatar/abc'), 'https://secure.gravatar.com/avatar/abc')
        self.assertEqual(image_extension(b'<svg><image href="https://evil.invalid"/></svg>'), '')
        self.assertEqual(image_extension(png()), '.png')

    def test_real_http_download_deduplicated_and_private_without_cross_host_credentials(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                calls.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(png())
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:' + str(httpd.server_port) + '/avatar.png'
            with tempfile.TemporaryDirectory() as folder, patch('avatars.avatar_url', return_value=url), patch.object(backend, 'REQUEST_INTERVAL', 0):
                client = backend.Client('gitlab.com', 'private-token')
                rows = [dict(avatarUrl=url), dict(avatarUrl=url)]
                hydrate_avatars(client, rows, Path(folder))
                hydrate_avatars(client, rows, Path(folder))
                self.assertEqual(calls, [None])
                self.assertEqual(rows[0]['avatar'], rows[1]['avatar'])
                cached = Path(rows[0]['avatar'].removeprefix('file://'))
                self.assertEqual(cached.read_bytes(), png())
                self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
                self.assertEqual(client.requests, 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
