"""Persistent visual actions, source identity, safe reuse and short edits.
All annotations in these tests are explicitly synthetic fixtures, never demo data.
"""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.ai import Nova
from backend.catalog import Catalog
from backend.logbook import Logbook,VERSION,source_key,normalize_evidence,validate_batch,build,recommend_stored
from backend.pipeline import Pipeline
from backend.service import initial_state

class LogbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'assets').mkdir();sqlite3.connect(self.root/'assets/db.sqlite').close()
        self.video=self.root/'video.mp4';self.video.write_bytes(b'controlled fixture video')
        self.catalog=Catalog(self.root);self.m=dict(id='fixture',title='Fixture only',video='video.mp4',transcript=None,characters=[dict(id='c1',name='Fixture')],_folder=self.root)
        self.store=Logbook(self.catalog);self.store.schema();self.ai=Nova(self.root/'runtime',key='')
        self.pipeline=Pipeline(self.catalog,self.ai,self.root/'runtime')
    def tearDown(self):self.tmp.cleanup()
    def event(self,id='a',start=10,end=20,threads=None):
        return dict(id=id,title='Fixture action',summary='Controlled fixture',category='critical',why='Fixture only',characters=['c1'],relationships=[],threads=threads or ['fixture'],ranges=[dict(start=start,end=end)],start=start,end=end,evidence='Fixture',frame_times=[start+1],visual_scene=True)
    def stored(self,events=None):
        return dict(version=11,logbook_version=VERSION,id='fixture',title='Fixture',duration=100,events=events or [self.event()],timeline=[dict(start=0,end=100,title='Future fixture fact',summary='Secret ending',why='Future fixture',category='critical',basis='sampled_visual')],provider='Fixture vision',model='fixture-only',created_at='fixture',source_key=source_key(self.catalog,self.m),persistent_logbook=True)
    def save(self,data):
        with sqlite3.connect(self.store.db) as c:c.execute('INSERT INTO action_logbook_run VALUES (?,?,?,?,?,?,?,?)',('fixture',data['source_key'],VERSION,'Fixture vision','fixture-only','fixture',100,json.dumps(data)))
    def test_inputs_and_cues_allow_no_transcript(self):
        from backend.watched_scope import scoped_cues
        self.assertEqual(self.catalog.inputs(self.m),(self.video,None));self.assertEqual(scoped_cues(None,self.m),[])
    def test_persistent_source_hash_is_portable_across_mtime_and_model_changes(self):
        import os
        original=source_key(self.catalog,self.m);self.video.touch()
        with patch.dict(os.environ,{'NOVA_MODEL':'different'}):self.assertEqual(original,source_key(self.catalog,self.m))
        self.video.write_bytes(b'changed fixture media');self.assertNotEqual(original,source_key(self.catalog,self.m))
    def test_changed_source_cannot_reuse_stale_logbook(self):
        self.save(self.stored());self.assertIsNotNone(self.store.load(self.m));self.video.write_bytes(b'changed source');self.assertIsNone(self.store.load(self.m))
    def test_projection_withholds_partial_and_skipped_action_claims(self):
        data=self.stored([self.event('seen',10,20),self.event('partial',25,35),self.event('skipped',50,65)]);data['omitted_after_caption_review']=[dict(why='Secret ending from rejected future edit')]
        projected=self.store.project(dict(self.m,watched_ranges=[[0,30],[60,80]]),data,'fixture')
        self.assertEqual([e['id'] for e in projected['events']],['seen'])
        self.assertNotIn('Secret ending',json.dumps(projected));self.assertNotIn('Future fixture fact',json.dumps(projected))
        self.assertTrue(any(t['category']=='unwatched' for t in projected['timeline']))
    def test_pipeline_reuses_sqlite_without_key_or_model_call(self):
        self.save(self.stored());m=dict(self.m,watched_ranges=[[0,30]])
        with patch.object(self.ai,'json',side_effect=AssertionError('No API call allowed')),patch.object(self.pipeline,'ffprobe',side_effect=AssertionError('No reanalysis allowed')):
            analysis,folder=self.pipeline.analyze(m,lambda *a:None)
            self.assertTrue(analysis['persistent_logbook']);self.assertEqual(analysis['events'][0]['id'],'a')
            self.assertTrue((folder/'analysis.json').is_file())
    def test_build_completed_source_is_a_no_op_without_key(self):
        self.save(self.stored())
        with patch.object(self.ai,'json',side_effect=AssertionError('No API call allowed')):self.assertEqual(build(self.pipeline,self.m)['model'],'fixture-only')
    def test_missing_logbook_and_key_is_explicit_failure(self):
        with self.assertRaisesRegex(RuntimeError,'AI unavailable'):build(self.pipeline,self.m)
    def test_short_edits_use_only_meaningful_ranges_and_no_padding(self):
        e=self.event();e['ranges']=[dict(start=10,end=14),dict(start=18,end=22)];e['end']=22
        a=self.stored([e]);state=initial_state();m=dict(self.m,watched_ranges=[[0,30]])
        with patch.object(self.pipeline,'render',return_value='/generated/fixture.mp4') as render:
            q=recommend_stored(self.pipeline,m,a,self.root,state,[e],lambda *a:None)
        self.assertEqual(q['items'][0]['duration'],8);self.assertEqual(render.call_args.args[2],e['ranges']);self.assertEqual(q['selection_mode'],'local ranking of persistent AI edits')
    def test_under_seven_over_fortyfive_and_future_edits_are_rejected(self):
        events=[self.event('tiny',1,7),self.event('long',1,47),self.event('future',40,50)]
        with patch.object(self.pipeline,'render') as render:
            q=recommend_stored(self.pipeline,dict(self.m,watched_ranges=[[0,30]]),self.stored(events),self.root,initial_state(),events,lambda *a:None)
        self.assertEqual(q['items'],[]);render.assert_not_called()
    def test_forgetting_prioritizes_related_fresh_actions_without_repeated_footage(self):
        old=self.event('old',40,50,['goal']);related=self.event('related',10,20,['goal']);other=self.event('other',65,75,['other'])
        state=initial_state();state['memory']={'old':0};state['last_rated']='old';state['clip_history']=[dict(event_id='old',ranges=old['ranges'])]
        with patch.object(self.pipeline,'render',return_value='/generated/fixture.mp4'):
            q=recommend_stored(self.pipeline,self.m,self.stored([old,related,other]),self.root,state,[related,other],lambda *a:None)
        self.assertEqual(q['items'][0]['event_id'],'related')
    def test_db_tags_ignore_characters_only_in_omitted_footage(self):
        from backend.story_map import tag_events
        m=dict(media_id=1,original_start_seconds=0,characters=[dict(id='c1',metadata_presence={'1':[{'00:00:12':'00:00:14'}]}),dict(id='c2',metadata_presence={'1':[{'00:00:18':'00:00:20'}]})],supplied_relationships=[])
        e=self.event();e.update(ranges=[dict(start=10,end=12),dict(start=18,end=23)],end=23)
        tag_events(m,[e]);self.assertNotIn('c1',e['characters']);self.assertIn('c2',e['characters'])
    def test_nova_accepts_literal_linebreak_string_but_rejects_broken_structure(self):
        self.assertEqual(Nova.decode_arguments('{"evidence":"line one\nline two"}'),{'evidence':'line one\nline two'})
        for bad in ('{"evidence":"unfinished','{"x": 2,}','{"x":"nul\x00"}'):
            with self.assertRaises(ValueError):Nova.decode_arguments(bad)
    def test_sample_edge_rounding_is_normalized_without_filling_a_real_gap(self):
        b=self.batch();b['segments'][0].update(start=.05,end=19.95)
        ready,_=normalize_evidence(b,0,20,[2,12],[])
        self.assertEqual((ready['segments'][0]['start'],ready['segments'][0]['end']),(0,20))
        validate_batch(ready,0,20,[2,12],[])
        b['segments'][0]['start']=2
        ready,_=normalize_evidence(b,0,20,[2,12],[])
        with self.assertRaises(ValueError):validate_batch(ready,0,20,[2,12],[])
    def test_invalid_proposed_edit_does_not_discard_valid_ai_action(self):
        from backend.logbook import finalize_batch
        b=self.batch();b['moments'][0]['ranges']=[dict(start=0,end=3)]
        ready,changes,omitted=finalize_batch(b,0,20,[2,12],[])
        self.assertTrue(omitted);self.assertEqual(ready['moments'][0]['ranges'],[dict(start=0,end=20)])

    def batch(self):
        evidence=dict(visual_evidence='Controlled fixture observation',frame_times=[2,12],cue_ids=[])
        return dict(segments=[dict(start=0,end=20,category='critical',title='Fixture',summary='Fixture only',why='Fixture only',**evidence)],moments=[dict(title='Fixture',summary='Fixture only',category='critical',why='Fixture only',ranges=[dict(start=0,end=15)],threads=['fixture'],**evidence)])
    def test_validator_rejects_filler_in_montage(self):
        b=self.batch();b['segments'][0]['category']='filler'
        with self.assertRaisesRegex(ValueError,'overlaps filler'):validate_batch(b,0,20,[2,12],[])
    def test_validator_rejects_cut_out_evidence_and_tiny_moments(self):
        for kind in ('frame','short'):
            b=self.batch();b['moments'][0]['ranges']=[dict(start=0,end=10 if kind=='frame' else 6)]
            with self.assertRaises(ValueError):validate_batch(b,0,20,[2,12],[])
    def test_normalizer_drops_outside_citations_without_inventing_frames(self):
        b=self.batch();b['moments'][0]['frame_times']=[2,12,18,999]
        normalized,changes=normalize_evidence(b,0,20,[2,12,18],[])
        self.assertEqual(normalized['moments'][0]['frame_times'],[2,12]);self.assertTrue(changes)
        validate_batch(normalized,0,20,[2,12,18],[])
    def test_adjacent_short_ai_actions_combine_without_filler_padding(self):
        from backend.logbook import finalize_batch
        b=self.batch();first=dict(b['segments'][0],start=0,end=4,frame_times=[2]);second=dict(first,start=4,end=9,frame_times=[6]);third=dict(first,start=9,end=20,category='filler',frame_times=[12])
        ready,_,_=finalize_batch(dict(segments=[first,second,third],moments=[]),0,20,[2,6,12],[])
        self.assertEqual(ready['moments'][0]['ranges'],[dict(start=0,end=9)])

    def test_filtering_character_and_pair_happens_before_local_selection(self):
        a=self.stored([self.event('one'),dict(self.event('two',30,40),characters=['c2'])]);state=initial_state();state['character']='c1'
        with patch.object(self.pipeline,'render',return_value='/generated/fixture.mp4'),patch.object(self.ai,'json',side_effect=AssertionError('No model call')):
            q=self.pipeline.recommend(self.m,a,self.root,state,lambda *a:None)
        self.assertEqual([i['event_id'] for i in q['items']],['one'])

if __name__=='__main__':unittest.main()
