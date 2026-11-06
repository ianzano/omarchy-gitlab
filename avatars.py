"""Bounded, private avatar cache. Never sends GitLab credentials to another host."""
import hashlib
import os
import struct
import tempfile
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

from backend import ClientError, NoRedirect, reset_time


def avatar_url(host, value):
    if value.startswith('/') and not value.startswith('//'):
        value = 'https://' + host + value
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.username or parts.password or parts.fragment:
        return ''
    if parts.netloc.lower() not in (host, 'secure.gravatar.com', 'www.gravatar.com', 'gravatar.com'):
        return ''
    return value


def image_extension(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        width, height = struct.unpack('>II', data[16:24])
        return '.png' if 0 < width <= 2048 and 0 < height <= 2048 else ''
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith((b'GIF87a', b'GIF89a')) and len(data) >= 10:
        width, height = struct.unpack('<HH', data[6:10])
        return '.gif' if 0 < width <= 2048 and 0 < height <= 2048 else ''
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    return ''  # SVG is intentionally not rendered from network data.


def hydrate_avatars(client, rows, directory):
    folder = directory / 'avatars'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        return
    records = client.state.setdefault('avatars', {})
    for row in rows:
        row['avatar'] = ''
        url = avatar_url(client.host, row.get('avatarUrl', ''))
        if not url:
            continue
        key = hashlib.sha256(url.encode()).hexdigest()
        saved = records.get(key, {})
        path = folder / saved.get('file', 'missing')
        if path.parent != folder or path.is_symlink():
            continue
        if saved.get('file') and path.is_file():
            row['avatar'] = path.as_uri()
        if time.time() < saved.get('expiresAt', 0):
            continue
        try:
            timeout = client.reserve_request()
            headers = {'User-Agent': 'omarchy-gitlab-pipelines/1.3', 'Accept': 'image/png,image/jpeg,image/webp,image/gif'}
            if client.token and urlsplit(url).netloc.lower() == client.host:
                headers['Authorization'] = 'Bearer ' + client.token
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.build_opener(NoRedirect).open(request, timeout=min(8, timeout)) as response:
                data = response.read(512 * 1024 + 1)
                if urlsplit(url).netloc.lower() == client.host:
                    client.observe_headers(response.headers)
            extension = image_extension(data) if len(data) <= 512 * 1024 else ''
            if not extension:
                raise ValueError('Unsupported avatar')
            target = folder / (key + extension)
            fd, temporary = tempfile.mkstemp(prefix='.avatar-', dir=folder)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            records[key] = dict(file=target.name, expiresAt=time.time() + 86400)
            row['avatar'] = target.as_uri()
        except ClientError:
            break  # Shared budget/cooldown applies to avatars too.
        except urllib.error.HTTPError as error:
            if error.code == 429:
                client.state['blockedUntil'] = max(time.time() + 65, reset_time(error.headers, time.time()) + 5)
            records[key] = dict(saved, expiresAt=time.time() + 600)
            error.close()
            if error.code == 429:
                break
        except (OSError, ValueError, TimeoutError):
            records[key] = dict(saved, expiresAt=time.time() + 600)
