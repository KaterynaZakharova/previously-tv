"""Replay the silent-scene cue-list failure without weakening evidence contracts."""
import copy,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.ai import Nova,AIResponseInvalid
from backend import schemas
from backend.scenes import analyze_scenes

class Response(io.BytesIO):
    status=200;headers={}
    def __init__(self,value):
        raw=dict(choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(type='function',function=dict(name='emit_result',arguments=json.dumps(value)))]))])
        self.raw=raw;super().__init__(json.dumps(raw).encode())

def scene(index=0,ids=None,visual='A figure lifts a visible object.'):
    return dict(index=index,category='critical',title='Controlled fixture action',summary='A test figure lifts an object.',why='Fixture evidence only',characters=[],visual_evidence=visual,evidence_cue_ids=ids or [])

class SceneCueValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.ai=Nova(self.root,key='fixture-key-only',model='fixture-vision',interval=0)
    def tearDown(self):self.tmp.cleanup()
    def test_exact_silent_scene_maxitems_failure_is_cleaned_before_schema_check(self):
        answer=dict(scenes=[scene(0),scene(1),scene(2,[34,35])]);schema=schemas.scenes_for(set(),[0,1,2],set());response=Response(answer)
        with self.assertRaisesRegex(ValueError,r'\$\.scenes\[2\].evidence_cue_ids: too many array items'):Nova.checked_parse(response.raw,schema)
        data=dict(intervals=[dict(index=i,cues=[]) for i in range(3)])
        with patch('urllib.request.urlopen',return_value=response) as http:
            actual=self.ai.json('scene_coverage','Use only local scene evidence.',data,schema)
        self.assertEqual(http.call_count,1);self.assertEqual(actual['scenes'][2]['evidence_cue_ids'],[]);self.assertEqual(actual['scenes'][2]['visual_evidence'],answer['scenes'][2]['visual_evidence'])
        with patch('urllib.request.urlopen',side_effect=AssertionError('Should reuse cache')):self.assertEqual(self.ai.json('scene_coverage','Use only local scene evidence.',data,schema),actual)
        log=(self.root/'logs/ai-responses.jsonl').read_text();self.assertIn('cue_references_filtered',log);self.assertNotIn('schema_retry',log)
    def test_normalization_does_not_mutate_raw_tool_arguments(self):
        answer=dict(scenes=[scene(0,[123])]);raw=Response(answer).raw
        raw['choices'][0]['message']['tool_calls'][0]['function']['arguments']=copy.deepcopy(answer)
        actual=self.ai.checked_task_parse(raw,schemas.scenes_for(set(),[0],set()),'scene_coverage',dict(intervals=[dict(index=0,cues=[])]))
        self.assertEqual(actual['scenes'][0]['evidence_cue_ids'],[])
        self.assertEqual(raw['choices'][0]['message']['tool_calls'][0]['function']['arguments']['scenes'][0]['evidence_cue_ids'],[123])
    def test_cross_scene_unknown_and_duplicate_cue_ids_are_removed(self):
        answer=dict(scenes=[scene(0,[7,7,8,999]),scene(1,[7,8])]);data=dict(intervals=[dict(index=0,cues=[dict(id=7)]),dict(index=1,cues=[dict(id=8)])])
        with patch('urllib.request.urlopen',return_value=Response(answer)):
            result=self.ai.json('scene_coverage','Use exact evidence.',data,schemas.scenes_for(set(),[0,1],{7,8}))
        self.assertEqual([s['evidence_cue_ids'] for s in result['scenes']],[[7],[8]])
    def test_bad_types_and_other_tasks_still_fail_strict_validation(self):
        self.ai.max_schema_retries=0
        for task,ids in [('scene_coverage',['7']),('unrelated_task',[7])]:
            with patch('urllib.request.urlopen',return_value=Response(dict(scenes=[scene(0,ids)]))):
                with self.assertRaises(AIResponseInvalid):self.ai.json(task,'Fixture.',dict(intervals=[dict(index=0,cues=[])]),schemas.scenes_for(set(),[0],set()))
    def test_silent_visual_scene_survives_full_scene_pipeline_with_no_invented_cues(self):
        p=SimpleNamespace(ai=self.ai);m=dict(media_id=1,characters=[],watched_ranges=[[0,30]])
        with patch('backend.scenes.frame_image',return_value='data:image/jpeg;base64,dGVzdA=='),patch('urllib.request.urlopen',return_value=Response(dict(scenes=[scene(0,[1,2,3])]))):
            timeline,events,links=analyze_scenes(p,m,self.root/'fixture.mp4',self.root,30,[],[],lambda *a:None)
        self.assertEqual(timeline[0]['basis'],'sampled_visual');self.assertEqual(timeline[0]['evidence_cue_ids'],[]);self.assertTrue(events);self.assertEqual(links,[])
    def test_empty_visual_and_outside_dialogue_do_not_become_supported_critical_claim(self):
        p=SimpleNamespace(ai=self.ai);m=dict(media_id=1,characters=[],watched_ranges=[[0,30]])
        with patch('backend.scenes.frame_image',return_value='data:image/jpeg;base64,dGVzdA=='),patch('urllib.request.urlopen',side_effect=lambda *a,**k:Response(dict(scenes=[scene(0,[7],visual='')]))):
            timeline,events,links=analyze_scenes(p,m,self.root/'fixture.mp4',self.root,30,[],[],lambda *a:None)
        self.assertEqual(timeline[0]['category'],'unclassified');self.assertEqual(events,[])

if __name__=='__main__':unittest.main()
