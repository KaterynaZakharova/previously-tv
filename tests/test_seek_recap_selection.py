"""Seek regressions using the supplied films' saved annotations and fixture AI."""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.home import HomeService
from backend.logbook import Logbook
from backend.pipeline import Pipeline
from backend.service import Service, initial_state
from backend.recap_selection import order_for_playhead
from backend.logbook import recommend_stored

ROOT = Path(__file__).resolve().parents[1]


class SelectionAI:
    key = 'fixture'; model = 'fixture'; provider = 'nova'; label = 'Fixture'

    def __init__(self): self.calls = []
    def log(self, *args): pass

    def json(self, task, instruction, data, schema, progress=None, **kwargs):
        self.calls.append(copy.deepcopy(data))
        return dict(explanation='Fixture selection', items=[
            dict(event_id=e['id'], reason='Fixture context', ranges=e['allowed_ranges'])
            for e in [data['candidates'][i] for i in (-1, len(data['candidates'])//2, 0)]])


class SeekRecapSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        shutil.copytree(ROOT / 'assets', self.root / 'assets', ignore=shutil.ignore_patterns('*.mp4'))
        self.ai = SelectionAI()
        self.service = Service(self.root, self.runtime, self.ai)
        for manifest in self.service.catalog.manifests():
            video = self.root / manifest['video']
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b'fixture video')
        self.home = HomeService(self.root, self.runtime)
        self.service.home = self.home

    def tearDown(self): self.tmp.cleanup()

    def stored_queue(self, unit, episode_id, position, state=None):
        self.home.save('viewer', dict(episode_id=episode_id, position=position, watched_ranges=[[0, 2]]))
        manifest = self.service.manifest('viewer', unit)
        with sqlite3.connect(self.root / 'assets/db.sqlite') as db:
            stored = json.loads(db.execute('SELECT payload FROM action_logbook_run WHERE unit_id=?', (unit,)).fetchone()[0])
        analysis = Logbook(self.service.catalog).project(manifest, stored, 'fixture')
        with patch.object(self.service.pipeline, 'render', return_value='/generated/fixture.mp4'):
            queue = self.service.pipeline.recommend(manifest, analysis, self.runtime, state or initial_state(), lambda *args: None)
        events = {event['id']: event for event in analysis['events']}
        return queue, events

    def test_seek_near_end_selects_across_prefix_and_plays_chronologically(self):
        for unit, eid, position in [('spring_e01', 4, 420), ('sprite_fright_e01', 5, 540)]:
            with self.subTest(unit=unit):
                queue, events = self.stored_queue(unit, eid, position)
                selected = [events[item['event_id']] for item in queue['items']]
                self.assertTrue(selected)
                self.assertLess(selected[0]['start'], position / 3)
                self.assertEqual([e['start'] for e in selected], sorted(e['start'] for e in selected))
                self.assertTrue(any(e['start'] >= 2 * position / 3 for e in selected))
                self.assertTrue(any(position / 3 <= event['start'] < 2 * position / 3 for event in selected))
                self.assertTrue(all(event['end'] <= position for event in selected))
                self.assertEqual(self.ai.calls, [])  # Saved annotations still require no model call.

    def test_seek_to_middle_uses_middle_events_and_excludes_later_annotations(self):
        queue, events = self.stored_queue('sprite_fright_e01', 5, 300)
        selected = [events[item['event_id']] for item in queue['items']]
        self.assertLess(selected[0]['start'], 100)
        self.assertTrue(any(e['start'] >= 200 for e in selected))
        self.assertTrue(all(event['end'] <= 300 for event in selected))
        self.assertTrue(all(r['end'] <= 300 for item in queue['items'] for r in item['ranges']))

    def test_remembered_event_is_excluded_while_retaining_time_coverage(self):
        queue, events = self.stored_queue('sprite_fright_e01', 5, 540)
        remembered = queue['items'][0]['event_id']
        state = initial_state()
        state['memory'][remembered] = 1
        queue, events = self.stored_queue('sprite_fright_e01', 5, 540, state)
        self.assertNotIn(remembered, {item['event_id'] for item in queue['items']})
        self.assertTrue(any(events[item['event_id']]['start'] > 360 for item in queue['items']))

    def test_live_planner_receives_all_periods_and_returns_chronologically(self):
        events = [dict(id='scene_%02d' % i, title='Fixture', summary='Fixture', category='critical',
                       why='Fixture', characters=[], relationships=[], threads=[], start=i * 10, end=i * 10 + 8,
                       ranges=[dict(start=i * 10, end=i * 10 + 8)], visual_scene=True,
                       frame_times=[i * 10 + 2], evidence='Fixture') for i in range(50)]
        catalog = SimpleNamespace(inputs=lambda m: (self.root / 'fixture.mp4', None))
        pipeline = Pipeline(catalog, self.ai, self.runtime)
        with patch.object(pipeline, 'render', return_value='/generated/fixture.mp4'):
            queue = pipeline.recommend(dict(watched_ranges=[[0, 400]]), dict(events=events), self.runtime, initial_state(), lambda *args: None)
        sent = self.ai.calls[-1]
        self.assertEqual(sent['story_cutoff_seconds'], 400)
        self.assertTrue(any(e['allowed_ranges'][0]['start'] < 100 for e in sent['candidates']))
        self.assertTrue(any(e['allowed_ranges'][0]['start'] >= 300 for e in sent['candidates']))
        self.assertEqual(len(sent['candidates']), 18)
        byid = {event['id']: event for event in events}
        selected = [byid[item['event_id']] for item in queue['items']]
        self.assertLess(selected[0]['start'], 100)
        self.assertGreater(selected[-1]['start'], 300)
        self.assertEqual([e['start'] for e in selected], sorted(e['start'] for e in selected))
        self.assertTrue(any(133 <= event['start'] < 267 for event in selected))
        self.assertTrue(all(r['end'] <= 400 for item in queue['items'] for r in item['ranges']))


class NeutralSelectionTests(unittest.TestCase):
    def events(self):
        return [dict(id=str(i), start=i*10, end=i*10+8, ranges=[dict(start=i*10,end=i*10+8)],
                     title='Fixture',why='Fixture',category='critical', characters=['person'], threads=['goal']) for i in range(40)]

    def test_equal_time_buckets_and_chronological_playback(self):
        events=self.events()
        selected=order_for_playhead(events,dict(watched_ranges=[[0,400]]),dict(events=events),initial_state(),4)[:4]
        self.assertEqual([int(e['start']//100) for e in selected],[0,1,2,3])
        self.assertEqual([e['start'] for e in selected], sorted(e['start'] for e in selected))

    def test_forgotten_event_only_gets_related_earlier_setup(self):
        events=self.events();focus=events[20]
        events[19].update(characters=['other'],threads=['unrelated'])
        state=initial_state();state.update(memory={focus['id']:0},last_rated=focus['id'])
        selected=order_for_playhead(events,dict(watched_ranges=[[0,400]]),dict(events=events),state)
        self.assertTrue(selected)
        self.assertTrue(all(e['end']<=focus['start'] for e in selected))
        self.assertNotIn('19',{e['id'] for e in selected})
        self.assertEqual(selected[0]['id'],'18')

    def test_small_budget_resamples_both_halves_instead_of_truncating_tail(self):
        events=self.events();state=initial_state();state['budget']=20
        pipeline=SimpleNamespace(render=lambda *args:'/generated/fixture.mp4', catalog=SimpleNamespace(inputs=lambda m:('fixture.mp4',None)))
        queue=recommend_stored(pipeline,dict(watched_ranges=[[0,400]]),dict(events=events,model='fixture',provider='Fixture'),Path('/tmp'),state,events,lambda *a:None)
        starts=[i['ranges'][0]['start'] for i in queue['items']]
        self.assertEqual(len(starts),2)
        self.assertLess(starts[0],200);self.assertGreaterEqual(starts[1],200)


if __name__ == '__main__': unittest.main()
