"""Fictional controlled plans; no production annotations or provider calls."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.ai import Nova,AIUnavailable
from backend.pipeline import Pipeline,context_window,timed_cue,quote_cue_ids
from backend.service import initial_state
from backend import schemas

class RecommendationAI:
    model='fixture';key='test'
    def __init__(self,mode='mistyped'):self.mode=mode;self.calls=[];self.logs=[];self.invalidated=[]
    def log(self,record):self.logs.append(record)
    def invalidate(self,task=None):self.invalidated.append(task)
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,copy.deepcopy(data),copy.deepcopy(schema)))
        candidates=data['candidates']
        def item(e):return dict(event_id=e['id'],reason='Fixture context selection.',ranges=[dict(first=r['first'],last=r['last']) for r in e['context_ranges']])
        items=[item(e) for e in candidates]
        repair=bool(data.get('repair_feedback'))
        if self.mode in ('mistyped','always_mistyped') and (not repair or self.mode=='always_mistyped'):
            for i in range(1,len(items)):items[i]['event_id']='e00_0'+str(i-1)
        if self.mode=='duplicate' and not repair:items.insert(1,copy.deepcopy(items[0]))
        if self.mode=='tiny' and not repair:
            anchor=candidates[0]['anchor_cue_ids'][0];items[0]['ranges']=[dict(first=anchor,last=anchor)]
        if self.mode=='omitted_anchor' and not repair:
            first=candidates[0]['context_ranges'][0]['first'];items[0]['ranges']=[dict(first=first,last=first)]
        if self.mode=='always_invalid':
            for x in items:x['event_id']='unavailable'
        if self.mode=='budget' and repair:items=items[:1]
        return dict(explanation='Fixture queue, not movie interpretation.',items=items)

class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cues=[dict(id=i+1,start=(i//6)*100+(i%6)*3.,end=(i//6)*100+(i%6)*3.+2,text='Fixture line '+str(i+1)) for i in range(24)]
        ids=['e000_02','e001_00','e001_01','e001_02'];self.events=[]
        for i,eid in enumerate(ids):
            c=self.cues[i*6+2]
            self.events.append(dict(id=eid,title='Test '+eid,summary='A fixture fact',category='critical',characters=['alice'] if i%2==0 else ['bob'],threads=['fixture'],
                                    ranges=[dict(first=c['id'],last=c['id'],start=c['start']-.16,end=c['end']+.2)],anchor_cue_ids=[c['id']],relationships=[],start=c['start']))
        self.analysis=dict(events=self.events)
        self.catalog=SimpleNamespace(inputs=lambda m:(self.root/'movie.mp4',self.root/'transcript.srt'))
    def tearDown(self):self.tmp.cleanup()
    def recommend(self,ai,state=None):
        p=Pipeline(self.catalog,ai,self.root);rendered=[]
        def render(video,folder,ranges,progress):rendered.append(ranges);return '/generated/test.mp4'
        with patch('backend.pipeline.read_srt',return_value=self.cues),patch.object(p,'render',side_effect=render):
            result=p.recommend({},self.analysis,self.root,state or initial_state(),lambda *args:None)
        return result,rendered
    def test_exact_zero_padding_error_is_repaired_without_guessing_ids(self):
        ai=RecommendationAI();q,cuts=self.recommend(ai)
        self.assertEqual(len(ai.calls),2);self.assertEqual([x['event_id'] for x in q['items']],[e['id'] for e in self.events])
        self.assertEqual(len(cuts),4);self.assertIn("Unknown event_id 'e00_00'",ai.calls[1][1]['repair_feedback'][0])
        self.assertEqual(ai.invalidated,['recommend'])
    def test_schema_only_accepts_supplied_event_ids(self):
        schema=schemas.queue_for_candidates(['e000_02','e001_00'])
        with self.assertRaisesRegex(ValueError,'invalid enum'):
            Nova.validate_schema(dict(explanation='test',items=[dict(event_id='e00_00',reason='bad',ranges=[dict(first=69,last=69)])]),schema)
    def test_duplicates_trigger_repair_before_any_rendering(self):
        ai=RecommendationAI('duplicate');q,cuts=self.recommend(ai)
        self.assertEqual(len(ai.calls),2);self.assertEqual(len(cuts),4)
        self.assertEqual(len({x['event_id'] for x in q['items']}),4)
        self.assertTrue(any('Duplicate' in x for x in ai.calls[1][1]['repair_feedback']))
    def test_remaining_bad_ids_do_not_discard_verified_items(self):
        ai=RecommendationAI('always_mistyped');q,cuts=self.recommend(ai)
        self.assertEqual(len(ai.calls),3);self.assertEqual(len(cuts),1)
        self.assertEqual(q['items'][0]['event_id'],'e000_02');self.assertIn('omitted',q['explanation'])
    def test_all_invalid_choices_fail_explicitly_without_rendering(self):
        ai=RecommendationAI('always_invalid')
        with self.assertRaisesRegex(ValueError,'valid contextual recap'):self.recommend(ai)
        self.assertEqual(len(ai.calls),3)
    def test_tiny_clip_is_repaired_into_context(self):
        ai=RecommendationAI('tiny');q,_=self.recommend(ai)
        self.assertTrue(any('at least 12.0s' in x for x in ai.calls[1][1]['repair_feedback']))
        self.assertTrue(all(x['duration']>=12 for x in q['items']))
    def test_anchor_cannot_be_trimmed_away(self):
        ai=RecommendationAI('omitted_anchor');self.recommend(ai)
        self.assertTrue(any('anchor dialogue' in x for x in ai.calls[1][1]['repair_feedback']))
    def test_recommendation_gets_timecodes_and_wider_context(self):
        ai=RecommendationAI('valid');self.recommend(ai)
        e=ai.calls[0][1]['candidates'][0]
        self.assertLess(e['context_ranges'][0]['first'],e['ranges'][0]['first'])
        self.assertGreater(e['context_ranges'][0]['last'],e['ranges'][0]['last'])
        self.assertTrue(all({'id','start','end','start_timecode','end_timecode','text'}<=set(c) for c in e['cues']))
        self.assertEqual(ai.calls[0][2]['properties']['items']['items']['properties']['event_id']['enum'],[x['id'] for x in ai.calls[0][1]['candidates']])
    def test_feedback_and_character_filters_are_never_relaxed_by_repair(self):
        state=initial_state();state['character']='alice';state['memory']={'e000_02':1};state['last_rated']='e001_00'
        ai=RecommendationAI('valid');q,_=self.recommend(ai,state)
        self.assertEqual([x['event_id'] for x in q['items']],['e001_01'])
    def test_budget_repairs_to_fewer_contextual_clips(self):
        state=initial_state();state['budget']=20;ai=RecommendationAI('budget');q,_=self.recommend(ai,state)
        self.assertEqual(len(ai.calls),2);self.assertLessEqual(q['total_seconds'],20.5);self.assertEqual(len(q['items']),1)
    def test_nearly_identical_footage_is_not_repeated(self):
        ai=RecommendationAI('valid');context=context_window(self.events[0],self.cues)
        candidate=dict(self.events[0],context_ranges=context,min_clip_seconds=12)
        other=dict(candidate,id='different')
        answer=dict(items=[dict(event_id=i,reason='test',ranges=[dict(first=context[0]['first'],last=context[0]['last'])]) for i in (candidate['id'],'different')])
        plan,issues=Pipeline.validate_queue(answer,{candidate['id']:candidate,'different':other},self.cues,180)
        self.assertEqual(len(plan),1);self.assertIn('same footage',issues[0])
    def test_context_does_not_cross_a_long_dialogue_gap(self):
        window=context_window(self.events[1],self.cues)
        self.assertGreaterEqual(window[0]['first'],7);self.assertLessEqual(window[-1]['last'],12)
    def test_adjacent_cue_ids_across_long_silence_are_not_merged(self):
        event=dict(self.events[0],ranges=[dict(first=6,last=6),dict(first=7,last=7)])
        windows=context_window(event,self.cues)
        self.assertEqual(len(windows),2)
        self.assertLess(windows[0]['end'],windows[1]['start']-35)
    def test_anchor_quote_can_cross_subtitle_boundaries(self):
        self.assertEqual(quote_cue_ids('b c',[dict(id=1,text='a b'),dict(id=2,text='c d')]),[1,2])
    def test_isolated_one_line_announcements_are_not_returned_as_clips(self):
        self.cues=[dict(id=1,start=100.,end=101.,text='Fixture progress announcement')]
        self.events[:]=[dict(self.events[0],ranges=[dict(first=1,last=1,start=99.84,end=101.2)],anchor_cue_ids=[1])]
        ai=RecommendationAI('valid');q,cuts=self.recommend(ai)
        self.assertEqual(q['items'],[]);self.assertEqual(cuts,[]);self.assertEqual(ai.calls,[])
        self.assertIn('isolated',q['explanation'])
    def test_new_default_model_and_explicit_override(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(Nova(self.root,key='test').model,'auto')
        with patch.dict(os.environ,{'NOVA_MODEL':'nova-pro-v1'}):self.assertEqual(Nova(self.root,key='test').model,'nova-pro-v1')

class GapAI:
    key='test';model='fixture'
    def __init__(self,mode='valid'):self.mode=mode;self.logs=[];self.calls=[]
    def log(self,x):self.logs.append(x)
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append(data)
        if self.mode=='unavailable':raise AIUnavailable('Fixture outage')
        return dict(assessments=[dict(index=x['index'],category='critical',why='Probable continuity of the same fixture topic; visuals unknown.',
                                    evidence_cue_ids=[x['before'][-1]['id']]+([] if self.mode=='one_sided' else [x['after'][0]['id']])) for x in data['intervals']])
class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cues=[dict(id=1,start=2.,end=4.,text='Fixture topic.'),dict(id=2,start=40.,end=42.,text='Same fixture topic.')]
        self.spans=[dict(start=c['start'],end=c['end'],category='critical',basis='dialogue',why='Fixture') for c in self.cues]
    def tearDown(self):self.tmp.cleanup()
    def test_gap_inference_has_distinct_provenance_and_bracketing_evidence(self):
        ai=GapAI();p=Pipeline(None,ai,self.tmp.name);timeline=p.assess_gaps(p.timeline(self.spans,60),self.cues,lambda *args:None)
        inferred=[x for x in timeline if x.get('basis')=='inferred_context']
        self.assertEqual(len(inferred),1);self.assertEqual((inferred[0]['start'],inferred[0]['end']),(4,40))
        self.assertEqual(inferred[0]['evidence_cue_ids'],[1,2]);self.assertEqual(timeline[-1]['category'],'unclassified')
        self.assertAlmostEqual(sum(x['end']-x['start'] for x in timeline),60)
    def test_one_sided_inference_does_not_relabel_unknown_visuals(self):
        ai=GapAI('one_sided');p=Pipeline(None,ai,self.tmp.name);base=p.timeline(self.spans,60)
        self.assertEqual(p.assess_gaps(copy.deepcopy(base),self.cues,lambda *args:None),base)
        self.assertTrue(ai.logs)
    def test_gap_api_failure_keeps_existing_verified_dialogue(self):
        ai=GapAI('unavailable');p=Pipeline(None,ai,self.tmp.name);base=p.timeline(self.spans,60)
        self.assertEqual(p.assess_gaps(copy.deepcopy(base),self.cues,lambda *args:None),base)
        self.assertIn('outage',ai.logs[0]['error'])
    def test_rejected_event_only_unclassifies_its_overlap(self):
        ai=GapAI();p=Pipeline(None,ai,self.tmp.name)
        events=[dict(id='a',start=2,relationships=[],ranges=[dict(start=2,end=4)]),dict(id='b',start=10,relationships=[],ranges=[dict(start=10,end=12)])]
        review=dict(events=[dict(index=0,supported=True,reason='ok',unsupported_relationships=[]),dict(index=1,supported=False,reason='unsupported',unsupported_relationships=[])],unsupported_coverage=[])
        accepted,spans,_=p.apply_review(review,events,[dict(start=0,end=30,category='critical',basis='dialogue')],[],self.cues)
        self.assertEqual([x['category'] for x in spans],['critical','unclassified','critical'])
        self.assertEqual([(x['start'],x['end']) for x in spans],[(0,10),(10,12),(12,30)])
        self.assertEqual(len(accepted),1)

if __name__=='__main__':unittest.main()
