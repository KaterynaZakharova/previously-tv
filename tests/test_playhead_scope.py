"""Playback-position regressions: seeks, rewind, HTTP clips and voice evidence."""
import copy
import http.cookiejar
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from backend.chat import Chat
from backend.home import HomeService
from backend.service import Service, initial_state
from backend.watched_scope import scoped_cues
from server import create_server

ROOT = Path(__file__).resolve().parents[1]


class CutoffAI:
    key = 'fixture'; model = 'fixture'; provider = 'nova'; label = 'Fixture'

    def __init__(self): self.calls = []
    def log(self, *args): pass
    def invalidate(self, *args): pass

    def json(self, task, instruction, data, schema, **kwargs):
        self.calls.append(copy.deepcopy(data))
        evidence = data['evidence']
        return dict(answer='Fixture response', evidence_ids=[evidence[0]['id']] if evidence else [],
                    insufficient_evidence=not bool(evidence), finish=False)


class PlayheadScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        shutil.copytree(ROOT / 'assets', self.root / 'assets', ignore=shutil.ignore_patterns('*.mp4'))
        (self.root / 'frontend').mkdir()
        (self.root / 'frontend/index.html').write_text('fixture')
        self.home = HomeService(self.root, self.runtime)
        for manifest in Service(self.root, self.runtime, CutoffAI()).catalog.manifests():
            video = self.root / manifest['video']
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b'fixture video')
        self.ai = CutoffAI()
        self.service = Service(self.root, self.runtime, self.ai)
        self.service.home = self.home
        script = 'assets/cartoon/sprite_fright/transcript/playhead_test.json'
        (self.root / script).write_text(json.dumps(dict(lines=[
            dict(id=1, start=10, end=12, text='EARLY_FACT'),
            dict(id=2, start=90, end=100, text='SKIPPED_GAP_FACT'),
            dict(id=3, start=299, end=301, text='CROSSING_FUTURE_FACT'),
            dict(id=4, start=310, end=320, text='LATER_FUTURE_FACT'),
        ])))
        with sqlite3.connect(self.home.catalog.db) as db:
            db.execute('UPDATE transcript SET url=? WHERE episode_id=5', (script,))

    def tearDown(self): self.tmp.cleanup()

    def seek(self, position, sid='viewer', episode_id=5):
        return self.home.save(sid, dict(episode_id=episode_id, position=position, watched_ranges=[[0, 2]]))

    def test_two_seconds_played_then_seek_to_five_minutes_includes_entire_prefix(self):
        self.seek(300)
        manifest = self.service.manifest('viewer', 'sprite_fright_e01')
        self.assertEqual(manifest['watched_ranges'], [[0, 300]])
        self.assertTrue(self.home.can_recap('viewer', 'sprite_fright_e01'))
        cues = scoped_cues(self.service.catalog.inputs(manifest)[1], manifest)
        self.assertEqual([cue['text'] for cue in cues], ['EARLY_FACT', 'SKIPPED_GAP_FACT'])
        with sqlite3.connect(self.home.db) as db:
            self.assertEqual(db.execute('SELECT start,end FROM watched_range WHERE sid=? AND episode_id=5', ('viewer',)).fetchall(), [(0, 2)])
        restored = HomeService(self.root, self.runtime)
        episode = next(m for m in restored.get('viewer')['media'] if m['id'] == 3)['episodes'][0]
        self.assertTrue(episode['recap_ready'])
        self.assertEqual(episode['watched_ranges'], [[0, 300]])
        self.assertFalse(self.home.can_recap('other-viewer', 'sprite_fright_e01'))

    def test_rewind_shrinks_prefix_and_invalidates_old_queue(self):
        self.seek(300)
        manifest = self.service.manifest('viewer', 'sprite_fright_e01')
        state = dict(initial_state(), fingerprint=self.service.catalog.fingerprint(manifest), queue=dict(items=[dict(event_id='old')]))
        self.service.save('viewer', manifest['id'], state)
        self.seek(60)
        current = self.service.manifest('viewer', manifest['id'])
        self.assertEqual(current['watched_ranges'], [[0, 60]])
        self.assertNotEqual(self.service.catalog.fingerprint(current), state['fingerprint'])
        self.assertIsNone(self.service.movie('viewer', manifest['id'])['queue'])

    def test_chapter_cutoff_includes_unplayed_prior_chapters_and_excludes_later_ones(self):
        self.seek(60, episode_id=2)
        media = self.home.catalog.library()[0]
        episodes = media['episodes']
        spans = [self.home.episode_ranges('viewer', media, e) for e in episodes]
        self.assertEqual(spans[0], [[0, int(episodes[0]['duration'] * 1000) / 1000]])
        self.assertEqual(spans[1], [[0, 60]])
        self.assertEqual(spans[2], [])
        self.seek(400, episode_id=4)
        self.assertEqual(self.home.episode_ranges('viewer', self.home.catalog.library()[0], episodes[1]), [[0, 60]])

    def test_voice_includes_skipped_facts_but_not_cues_or_actions_crossing_cutoff(self):
        self.seek(300)
        chat = Chat(self.service, self.home)
        events = [dict(id='gap', title='SKIPPED_ACTION', summary='Earlier action', ranges=[dict(start=90, end=100)]),
                  dict(id='future', title='FUTURE_ACTION', summary='Later action', ranges=[dict(start=299, end=301)])]
        context = dict(mode='watch', media_id=3, episode_id=5, source_time=300)
        with patch.object(self.service, 'analysis', return_value=dict(events=events)):
            token = chat.start('viewer', context)['session_id']
            chat.ask('viewer', token, 'Explain the skipped gap')
        text = json.dumps(self.ai.calls[-1]['evidence'])
        self.assertIn('SKIPPED_GAP_FACT', text)
        self.assertIn('SKIPPED_ACTION', text)
        self.assertNotIn('FUTURE', text)
        self.assertTrue(all(row['end'] <= 300 for row in self.ai.calls[-1]['evidence']))
        # Voice from a recap must use its source timestamp, not the later movie playhead.
        with patch.object(self.service, 'analysis', return_value=None):
            token = chat.start('viewer', dict(context, mode='recap', source_time=60))['session_id']
            chat.ask('viewer', token, 'What happened?')
        self.assertNotIn('SKIPPED_GAP_FACT', json.dumps(self.ai.calls[-1]['evidence']))
        self.assertTrue(all(row['end'] <= 60 for row in self.ai.calls[-1]['evidence']))

    def test_http_previously_transcript_and_source_preview_after_seek(self):
        server = create_server(port=0, root=self.root, runtime=self.runtime, ai=self.ai)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json'})
            with client.open(req) as response: return json.load(response)

        try:
            request('/api/progress', dict(episode_id=5, position=300, watched_ranges=[[0, 2]]))
            self.assertEqual(request('/api/movie/sprite_fright_e01')['movie']['id'], 'sprite_fright_e01')
            cues = request('/api/movie/sprite_fright_e01/transcript')
            self.assertEqual(cues['watched_ranges'], [[0, 300]])
            self.assertEqual([row['text'] for row in cues['cues']], ['EARLY_FACT', 'SKIPPED_GAP_FACT'])
            with patch.object(server.service.pipeline, 'render', return_value='/generated/fixture.mp4') as render:
                self.assertEqual(request('/api/movie/sprite_fright_e01/source-preview', dict(start=90, end=100))['clip'], '/generated/fixture.mp4')
                with self.assertRaises(urllib.error.HTTPError):
                    request('/api/movie/sprite_fright_e01/source-preview', dict(start=295, end=305))
                self.assertEqual(render.call_count, 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
