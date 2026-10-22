#!/usr/bin/env python3
"""Python 3.9+; no pip dependencies. python server.py --host 0.0.0.0"""

import argparse
import json
import mimetypes
import math
import re
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs
from backend.catalog import ROOT, inside
from backend.service import Service
from backend.ai import is_configured, AIUnavailable, AIResponseInvalid
from backend.chat import Chat
from backend.voice import Speech
from backend.home import HomeService
from backend.media_catalog import MediaCatalog
from backend.watched_scope import scoped_cues, windows, covers


def load_env(root):
    path = Path(root) / '.env'
    if path.is_file():
        import os

        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if re.fullmatch('[A-Z][A-Z0-9_]*', key):
                    os.environ.setdefault(key, value.strip().strip('"\''))


def create_server(host='127.0.0.1', port=8000, root=ROOT, runtime=None, ai=None):
    root = Path(root)
    load_env(root)
    service = Service(root, runtime, ai)
    home = (
        HomeService(root, service.runtime, demo_start=True)
        if (root / 'assets/db.sqlite').is_file()
        else None
    )
    service.home = home
    chat = Chat(service, home)
    speech = Speech(service.ai)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def session(self):
            c = SimpleCookie()
            try:
                c.load(self.headers.get('Cookie', ''))
                sid = c['viewer'].value if 'viewer' in c else ''
            except Exception:
                sid = ''
            return sid if re.fullmatch('[a-f0-9]{32}', sid) else uuid.uuid4().hex

        def json_response(self, code, data, sid=None):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if sid:
                self.send_header(
                    'Set-Cookie',
                    f'viewer={sid}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000',
                )
            if code >= 400:
                self.close_connection = True
                self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            sid = self.session()
            try:
                if path == '/api/home':
                    if not home:
                        raise KeyError('No asset database')
                    return self.json_response(200, home.get(sid), sid)
                match = re.fullmatch(r'/api/story-map/(\d+)', path)
                if match and home:
                    values = parse_qs(urlsplit(self.path).query).get('at')
                    at = float(values[0]) if values else None
                    return self.json_response(
                        200, home.story_map(sid, int(match[1]), at=at), sid
                    )
                if path == '/api/library' and home:
                    cards = service.catalog.cards()
                    for card in cards:
                        if not home.transcript_visible(sid, card['id']):
                            card['synopsis'] = ''
                    return self.json_response(
                        200,
                        dict(
                            movies=cards,
                            logbook_ready=bool(cards)
                            and all(c.get('logbook_ready') for c in cards),
                            ai_configured=is_configured(service.ai),
                            model=service.ai.model,
                            provider=getattr(service.ai, 'label', 'AI'),
                            key_env=getattr(service.ai, 'key_env', 'NOVA_API_KEY'),
                        ),
                        sid,
                    )
                if path == '/api/library':
                    return self.json_response(
                        200,
                        dict(
                            movies=service.catalog.cards(),
                            ai_configured=is_configured(service.ai),
                            model=service.ai.model,
                            provider=getattr(service.ai, 'label', 'AI'),
                            key_env=getattr(service.ai, 'key_env', 'NOVA_API_KEY'),
                        ),
                        sid,
                    )
                match = re.fullmatch('/api/movie/([a-z0-9_-]+)', path)
                if match:
                    if home and not home.can_recap(sid, match[1]):
                        return self.json_response(
                            403,
                            dict(
                                error='Advance the playback position to at least 0:12 before opening a recap.'
                            ),
                            sid,
                        )
                    return self.json_response(200, service.movie(sid, match[1]), sid)
                match = re.fullmatch(
                    '/api/movie/([a-z0-9_-]+)/jobs/([a-f0-9]{32})', path
                )
                if match:
                    if home and not home.can_recap(sid, match[1]):
                        return self.json_response(
                            403,
                            dict(error='This recap is after your playback position.'),
                            sid,
                        )
                    return self.json_response(
                        200, service.job(sid, match[1], match[2]), sid
                    )
                match = re.fullmatch('/api/movie/([a-z0-9_-]+)/transcript', path)
                if match:
                    if home and not home.can_recap(sid, match[1]):
                        return self.json_response(
                            403,
                            dict(
                                error='No recap available before this playback position yet.'
                            ),
                            sid,
                        )
                    m = service.manifest(sid, match[1])
                    script = service.catalog.inputs(m)[1]
                    return self.json_response(
                        200,
                        dict(
                            cues=scoped_cues(script, m),
                            watched_ranges=m.get('watched_ranges'),
                        ),
                        sid,
                    )
                if path.startswith('/api/'):
                    raise KeyError('Unknown endpoint')
                if path.startswith('/assets/'):
                    file = MediaCatalog(root).public_asset(unquote(path).lstrip('/'))
                elif path.startswith('/inputs/'):
                    parts = unquote(path).split('/', 3)
                    if len(parts) != 4:
                        raise KeyError('Missing input')
                    m = service.catalog.get(parts[2])
                    file = inside(m['_folder'], parts[3])
                    allowed = {m.get('video'), m.get('transcript'), m.get('poster')} | {
                        c['photo'] for c in m.get('characters', [])
                    }
                    if parts[3] not in allowed:
                        raise KeyError('Input not public')
                    if (
                        home
                        and parts[3] == m.get('transcript')
                        and not home.transcript_visible(sid, m['id'])
                    ):
                        return self.json_response(
                            403,
                            dict(
                                error='Full transcript hidden. Use the transcript endpoint limited to your playback position.'
                            ),
                            sid,
                        )
                elif path.startswith('/generated/'):
                    name = unquote(path[len('/generated/') :])
                    if not re.fullmatch(
                        r'[a-z0-9_-]+/[a-f0-9]{20}/clips/[a-f0-9]{24}\.mp4', name
                    ):
                        raise KeyError('Unknown generated asset')
                    file = inside(service.runtime / 'artifacts', name)
                else:
                    file = inside(
                        root / 'frontend', unquote(path).lstrip('/') or 'index.html'
                    )
                return self.send_file(file)
            except (ValueError, KeyError, IndexError):
                return self.json_response(404, dict(error='Not found'))

        def do_POST(self):
            path = urlsplit(self.path).path
            match = re.fullmatch(
                '/api/movie/([a-z0-9_-]+)/(action|source-preview|signal)', path
            )
            chat_action = re.fullmatch(
                '/api/chat/(start|ask|finish|speak|transcribe)', path
            )
            if not match and not chat_action and path != '/api/progress':
                return self.json_response(404, dict(error='Unknown endpoint'))
            if self.headers.get('Origin') and urlsplit(
                self.headers['Origin']
            ).netloc != self.headers.get('Host'):
                return self.json_response(
                    403, dict(error='Cross-origin request blocked')
                )
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if (
                    not 0
                    < n
                    <= (
                        950000
                        if chat_action and chat_action[1] == 'transcribe'
                        else 16384
                    )
                ):
                    raise ValueError('Invalid body size')
                data = json.loads(self.rfile.read(n))
                if not isinstance(data, dict):
                    raise ValueError('Expected JSON object')
                sid = self.session()
                if chat_action:
                    action = chat_action[1]
                    if action == 'start':
                        return self.json_response(
                            200,
                            dict(
                                **chat.start(sid, data.get('context')),
                                speech=speech.capabilities(),
                            ),
                            sid,
                        )
                    if action == 'ask':
                        return self.json_response(
                            200,
                            chat.ask(sid, data.get('session_id'), data.get('question')),
                            sid,
                        )
                    if action == 'finish':
                        return self.json_response(
                            200, chat.finish(sid, data.get('session_id')), sid
                        )
                    if action == 'speak':
                        return self.json_response(
                            200,
                            speech.synthesize(
                                chat.speech_text(
                                    sid, data.get('session_id'), data.get('message_id')
                                )
                            ),
                            sid,
                        )
                    chat._owned(sid, data.get('session_id'))
                    return self.json_response(
                        200, speech.transcribe(data.get('audio_base64')), sid
                    )
                if path == '/api/progress':
                    if not home:
                        raise KeyError('No home catalog')
                    return self.json_response(200, home.save(sid, data), sid)
                if home and not home.can_recap(sid, match[1]):
                    return self.json_response(
                        403,
                        dict(
                            error='Advance the playback position to at least 0:12 before building a recap.'
                        ),
                        sid,
                    )
                if match[2] == 'signal':
                    return self.json_response(
                        200, service.signal(sid, match[1], data), sid
                    )
                if match[2] == 'source-preview':
                    m = service.manifest(sid, match[1])
                    a, b = data.get('start'), data.get('end')
                    if (
                        any(
                            type(x) not in (int, float) or not math.isfinite(x)
                            for x in (a, b)
                        )
                        or a < 0
                        or not 0 < b - a <= 90
                        or not covers(windows(m), a, b)
                    ):
                        raise ValueError(
                            'Preview must remain inside one watched portion (maximum 90 seconds).'
                        )
                    video = service.catalog.inputs(m)[0]
                    folder = (
                        service.runtime
                        / 'artifacts'
                        / m['id']
                        / service.catalog.fingerprint(m)
                    )
                    clip = service.pipeline.render(
                        video, folder, [dict(start=a, end=b)]
                    )
                    return self.json_response(200, dict(clip=clip), sid)
                return self.json_response(202, service.start(sid, match[1], data), sid)
            except (AIUnavailable, AIResponseInvalid) as e:
                return self.json_response(503, dict(error=str(e)))
            except KeyError:
                return self.json_response(
                    404, dict(error='Unknown or expired session/media')
                )
            except (ValueError, TypeError) as e:
                return self.json_response(400, dict(error=str(e)))

        def send_file(self, file):
            size = file.stat().st_size
            start, end = 0, size - 1
            status = 200
            if self.headers.get('Range'):
                match = re.fullmatch(
                    r'bytes=(\d*)-(\d*)', self.headers['Range'].strip()
                )
                if not match or not any(match.groups()):
                    return self.bad_range(size)
                a, b = match.groups()
                if not a:
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(size - 1, int(b)) if b else size - 1
                if start >= size or end < start:
                    return self.bad_range(size)
                status = 206
            self.send_response(status)
            self.send_header(
                'Content-Type',
                mimetypes.guess_type(str(file))[0] or 'application/octet-stream',
            )
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(max(0, end - start + 1)))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command == 'HEAD':
                return
            try:
                with file.open('rb') as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        b = f.read(min(65536, remaining))
                        if not b:
                            break
                        self.wfile.write(b)
                        remaining -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def bad_range(self, size):
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()

    server = ThreadingHTTPServer((host, port), Handler)
    server.service = service
    server.home = home
    server.chat = chat
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f'Previously TV: http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
