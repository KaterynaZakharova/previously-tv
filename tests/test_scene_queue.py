"""Queue invariants, scene provenance, and whole-cast coverage. Fixtures only."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.ai import Nova,AIUnavailable
from backend.service import Service
from backend.pipeline import Pipeline,write_json
from backend.scenes import scene_intervals,analyze_scenes
from backend import schemas

class QueueAI:
    key='fixture';model='fixture';label='Fixture AI'
    def __init__(self):self.calls=[];self.logs=[]
    def log(self,r):self.logs.append(r)

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);d=self.root/'assets/inputs/story';d.mkdir(parents=True)
        (d/'movie.mp4').write_bytes(b'fixture');(d/'script.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nFixture.');(d/'face.jpg').write_bytes(b'fixture')
        (d/'movie.json').write_text(json.dumps(dict(id='story',title='Story',video='movie.mp4',transcript='script.srt',characters=[dict(id=n,name=n,photo='face.jpg') for n in ('a','b','c')])))
        self.ai=QueueAI();self.s=Service(self.root,ai=self.ai);self.m=self.s.catalog.get('story')
        self.events=[dict(id='e'+str(i),characters=['a','b'] if i%2==0 else ['c'],relationships=[dict(source='a',target='b')] if i%2==0 else []) for i in range(12)]
        self.analysis=dict(events=self.events,fingerprint=self.s.catalog.fingerprint(self.m));self.folder=self.s.runtime/'artifacts/story'/self.analysis['fingerprint'];write_json(self.folder/'analysis.json',self.analysis)
        self.s.pipeline.analyze=lambda *args:(self.analysis,self.folder)
        def recommend(m,a,folder,state,progress):
            self.ai.calls.append(copy.deepcopy(state));eligible=[e for e in self.events if e['id'] not in state['exclude_event_ids'] and state['memory'].get(e['id'])!=1 and e['id']!=state['last_rated']]
            if state['character']:eligible=[e for e in eligible if state['character'] in e['characters']]
            if state['pair']:eligible=[e for e in eligible if e['relationships'] and set(state['pair'])=={'a','b'}]
            return dict(items=[dict(event_id=e['id'],ranges=[dict(start=int(e['id'][1:])*30,end=int(e['id'][1:])*30+20)],duration=20,clip='/generated/fixture.mp4') for e in eligible[:2]],explanation='Fixture queue',model='fixture',provider='Fixture AI')
        self.s.pipeline.recommend=recommend
    def tearDown(self):self.tmp.cleanup()
    def action(self,kind,**kw):
        job=self.s.start('viewer','story',dict(type=kind,**kw))
        for _ in range(500):
            job=self.s.job('viewer','story',job['id'])
            if job['status']!='running':break
            time.sleep(.002)
        self.assertEqual(job['status'],'ready',job.get('error'));return job
    def test_remember_preserves_order_and_uses_no_ai_even_without_key(self):
        j=self.action('build');before=j['queue']['items'];calls=len(self.ai.calls);self.ai.key=''
        with patch.object(self.s.pipeline,'analyze',side_effect=AssertionError('No analysis on Remember')):
            j=self.action('remember',event_id=before[0]['event_id'])
        self.assertEqual(j['queue']['items'],before[1:]);self.assertEqual(len(self.ai.calls),calls);self.assertEqual(j['state']['memory'][before[0]['event_id']],1)
    def test_queue_survives_restart_and_build_restores_without_ai(self):
        j=self.action('build');s2=Service(self.root,ai=QueueAI());s2.ai.key=''
        r=s2.start('viewer','story',dict(type='build'));self.assertEqual(r['status'],'ready');self.assertEqual(r['queue'],j['queue'])
    def test_character_appends_and_preserves_existing_queue(self):
        old=self.action('build')['queue']['items'];j=self.action('character',character='c');self.assertEqual(j['queue']['items'][:len(old)],old)
        self.assertTrue(j['added_event_ids']);self.assertTrue(all('c' in next(e for e in self.events if e['id']==eid)['characters'] for eid in j['added_event_ids']))
    def test_pair_appends_only_matching_scenes(self):
        old=self.action('build')['queue']['items'];j=self.action('pair',pair=['a','b']);self.assertEqual(j['queue']['items'][:len(old)],old)
        self.assertTrue(j['added_event_ids']);self.assertTrue(all(int(eid[1:])%2==0 for eid in j['added_event_ids']))
    def test_repeated_not_remember_adds_new_explanations_without_repeats(self):
        j=self.action('build');seen={q['event_id'] for q in j['queue']['items']}
        for _ in range(3):
            prior=j['queue']['items'];rated=prior[0]['event_id'];j=self.action('not_remember',event_id=rated)
            self.assertEqual(j['queue']['items'][len(j['added_event_ids']):],prior[1:]);self.assertTrue(j['added_event_ids']);self.assertFalse(seen&set(j['added_event_ids']));seen.update(j['added_event_ids'])
        self.assertEqual(j['state']['context_depth'],3)
    def test_failed_ai_keeps_pending_queue_and_remember_still_works(self):
        j=self.action('build');old=j['queue']['items'];self.ai.key='';job=self.s.start('viewer','story',dict(type='character',character='c'))
        for _ in range(100):
            job=self.s.job('viewer','story',job['id'])
            if job['status']!='running':break
            time.sleep(.003)
        self.assertEqual(job['status'],'unavailable');self.assertEqual(job['queue']['items'],old)
        j=self.action('remember',event_id=old[0]['event_id']);self.assertEqual(j['queue']['items'],old[1:])
    def test_explicit_retry_generates_new_context_instead_of_only_restoring_queue(self):
        old=self.action('build')['queue']['items'];calls=len(self.ai.calls)
        j=self.action('build',refresh=True)
        self.assertEqual(len(self.ai.calls),calls+1);self.assertEqual(j['queue']['items'][:len(old)],old);self.assertTrue(j['added_event_ids'])
    def test_duplicate_ratings_are_rejected(self):
        j=self.action('build');eid=j['queue']['items'][0]['event_id'];self.action('remember',event_id=eid)
        with self.assertRaisesRegex(ValueError,'current queue'):self.s.start('viewer','story',dict(type='remember',event_id=eid))

class SceneTests(unittest.TestCase):
    def test_scene_partition_covers_entire_source_without_micro_segments(self):
        for duration in (734.167,100,17,61,3600):
            cues=[dict(id=1,start=29,end=31,text='A sentence')]
            rows=scene_intervals(duration,cues);self.assertEqual(rows[0]['start'],0);self.assertEqual(rows[-1]['end'],duration)
            self.assertAlmostEqual(sum(r['end']-r['start'] for r in rows),duration)
            self.assertTrue(all(r['end']-r['start']>=12 for r in rows))
            self.assertFalse(any(c['start']<r['start']<c['end'] for c in cues for r in rows))
    def test_nova_uses_labeled_images_without_putting_base64_in_text_input(self):
        with tempfile.TemporaryDirectory() as d:
            a=Nova(d,key='fixture',model='fixture');p=a.build_payload('scene','Assess.',dict(x=1,_images=[dict(label='Reference a',url='data:image/jpeg;base64,ABCD')]),schemas.obj(ok=dict(type='boolean')),500)
            blocks=p['messages'][1]['content'];self.assertIsInstance(blocks,list);self.assertNotIn('ABCD',blocks[0]['text']);self.assertEqual(blocks[1]['text'],'Reference a');self.assertEqual(blocks[2]['type'],'image_url')
    def test_review_uses_indexes_not_cue_ids(self):
        schema=schemas.review_for([{},{}],[{},{},{}])
        with self.assertRaisesRegex(ValueError,'enum'):Nova.validate_schema(dict(events=[],unsupported_coverage=[69]),schema)
    def test_scene_constraints_do_not_restrict_free_text_or_confuse_index_with_cue(self):
        schema=schemas.scenes_for({'a','b'},[0],[7,8])
        result=dict(scenes=[dict(index=0,category='critical',title='A free text scene title',summary='A meaningful sentence',why='The plot changes',characters=['a'],visual_evidence='A frame observation',evidence_cue_ids=[7])])
        Nova.validate_schema(result,schema)
        self.assertNotIn('enum',schemas.S);self.assertNotIn('enum',schemas.I)
    def test_scene_graph_is_built_only_from_observed_coappearance(self):
        class AI:
            provider='nova'
            def log(self,r):pass
            def json(self,task,instruction,data,schema,progress,max_tokens):
                return dict(scenes=[dict(index=r['index'],category='critical',title='Fixture scene',summary='Two fixture characters appear.',why='Fixture context',characters=['a','b'],visual_evidence='Two fictional figures in injected fixture frames',evidence_cue_ids=[]) for r in data['intervals']])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a.jpg').write_bytes(b'a');(root/'b.jpg').write_bytes(b'b');(root/'c.jpg').write_bytes(b'c');p=Pipeline(None,AI(),root)
            m=dict(_folder=root,characters=[dict(id=n,name=n,photo=n+'.jpg') for n in 'abc'])
            with patch('backend.scenes.frame_image',return_value='data:image/jpeg;base64,TEST'):
                timeline,events,links=analyze_scenes(p,m,root/'movie.mp4',root,60,[],[],lambda *args:None)
            self.assertEqual(len(timeline),2);self.assertTrue(all(x['basis']=='sampled_visual' for x in timeline));self.assertEqual(len(links),1)
            self.assertEqual({links[0]['source'],links[0]['target']},{'a','b'});self.assertEqual(links[0]['basis'],'scene_coappearance');self.assertTrue(all(e['visual_scene'] for e in events))
    def test_full_cast_remains_visible_when_dialogue_only_names_two(self):
        # The profile omission path must retain supplied metadata, not invent identities.
        from test_project import ProjectTests,FixtureAI
        # Reuse the small synthetic-source integration setup without executing its tests.
        ProjectTests.setUpClass()
        try:
            root=ProjectTests.root;m=root/'assets/inputs/test_story';manifest=json.loads((m/'movie.json').read_text());manifest['characters'].append(dict(id='extra',name='Extra',photo='alice.jpg',identity_hint='Supplied lookout role'))
            (m/'movie.json').write_text(json.dumps(manifest))
            class Profiles(FixtureAI):
                def json(self,task,*args,**kwargs):
                    out=super().json(task,*args,**kwargs)
                    if task=='profiles':out['characters']=[c for c in out['characters'] if c['id']!='extra']
                    return out
            with tempfile.TemporaryDirectory() as rt:
                from backend.catalog import Catalog
                cat=Catalog(root);a,_=Pipeline(cat,Profiles(),rt).analyze(cat.get('test_story'),lambda *args:None)
                self.assertEqual({c['id'] for c in a['characters']},{'alice','bob','extra'});extra=next(c for c in a['characters'] if c['id']=='extra');self.assertEqual(extra['description'],'Supplied lookout role')
        finally:ProjectTests.tearDownClass()

if __name__=='__main__':unittest.main()

class CastVerificationTests(unittest.TestCase):
    def run_recovery(self,visible):
        from backend.scenes import recover_cast
        class AI:
            provider='nova'
            def __init__(self):self.logs=[];self.calls=[]
            def log(self,r):self.logs.append(r)
            def json(self,task,instruction,data,schema,progress,max_tokens):
                self.calls.append(task)
                if task=='cast_recovery':return dict(observations=[dict(scene_index=0,character='c',frame_time=.9,companions=['a'],visual_evidence='An initial unverified claim.',category='secondary',why='Fixture setup')])
                if task=='cast_verify':return dict(visible=visible,source_evidence='Independent source frame evidence.' if visible else 'Only scenery, target absent.')
                raise AssertionError(task)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for n in 'abc':(root/(n+'.jpg')).write_bytes(b'fixture')
            ai=AI();p=Pipeline(None,ai,root);m=dict(_folder=root,characters=[dict(id=n,name=n,photo=n+'.jpg') for n in 'abc'])
            events=[dict(visual_scene=True,start=0,characters=['a','b'],relationships=[dict(source='a',target='b')])]
            with patch('backend.scenes.frame_image',return_value='data:image/jpeg;base64,FIXTURE'):
                added,edges=recover_cast(p,m,root/'movie.mp4',root,[dict(start=0,end=30)],events,[],lambda *args:None)
            return added,edges,ai
    def test_false_recovery_cannot_add_a_character_clip_or_edge(self):
        added,edges,ai=self.run_recovery(False);self.assertEqual(added,[]);self.assertEqual(edges,[]);self.assertIn('cast_verify',ai.calls)
        self.assertTrue(any(r.get('status')=='appearance_rejected' for r in ai.logs))
    def test_confirmed_recovery_uses_independent_source_evidence(self):
        added,edges,ai=self.run_recovery(True);self.assertEqual(len(added),1);self.assertTrue(edges)
        self.assertEqual(added[0]['evidence'],'Independent source frame evidence.');self.assertIn('c',added[0]['characters'])

class ExpansionTests(unittest.TestCase):
    def test_low_character_pool_searches_unused_intervals_and_persists_new_evidence(self):
        from types import SimpleNamespace
        from backend.service import initial_state
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'artifacts';folder.mkdir();ai=SimpleNamespace(provider='nova');catalog=SimpleNamespace(inputs=lambda m:(root/'movie.mp4',root/'script.srt'))
            p=Pipeline(catalog,ai,root);a=dict(events=[dict(id='old',characters=['a'],relationships=[],visual_scene=True,ranges=[dict(start=0,end=30)])],relationships=[],timeline=[dict(start=0,end=30),dict(start=30,end=60)])
            write_json(folder/'analysis.json',a);state=initial_state();state['character']='a';state['context_depth']=2;state['clip_history']=[dict(event_id='old',ranges=[dict(start=0,end=20)])]
            new=dict(id='new',characters=['a'],relationships=[],visual_scene=True,ranges=[dict(start=30,end=60)])
            with patch('backend.pipeline.read_srt',return_value=[]),patch('backend.pipeline.recover_cast',return_value=([new],[])) as recover:
                result=p.expand_context(dict(id='movie'),a,folder,state,lambda *args:None)
            self.assertEqual(recover.call_args.kwargs['targets'],{'a'});self.assertTrue(recover.call_args.kwargs['exclude_ranges']);self.assertEqual(len(recover.call_args.kwargs['fractions']),5)
            self.assertEqual({e['id'] for e in result['events']},{'old','new'});self.assertEqual(json.loads((folder/'analysis.json').read_text()),result)
    def test_sufficient_existing_context_does_not_rescan_the_movie(self):
        from types import SimpleNamespace
        from backend.service import initial_state
        with tempfile.TemporaryDirectory() as d:
            p=Pipeline(None,SimpleNamespace(provider='nova'),d);state=initial_state();state['character']='a'
            a=dict(events=[dict(id='e'+str(i),characters=['a'],relationships=[]) for i in range(3)])
            with patch('backend.pipeline.recover_cast',side_effect=AssertionError('Unnecessary scan')):
                self.assertIs(p.expand_context({},a,Path(d),state,lambda *args:None),a)
