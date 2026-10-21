"""Regression for the reported escaped-quote rejection; review responses are fixtures."""
import copy,json,tempfile,unittest
from pathlib import Path
from backend.pipeline import Pipeline,quote_is_supported
from backend.ai import Nova

FIXTURE=json.loads((Path(__file__).parent/'fixtures/escaped_evidence.json').read_text(encoding='utf-8'))

class ReviewFixture:
    key='test-only';model='fixture-only'
    def __init__(self,always_reject=False):self.calls=[];self.logs=[];self.invalidated=[];self.always_reject=always_reject
    def log(self,record):self.logs.append(record)
    def invalidate(self,task=None):self.invalidated.append(task)
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,copy.deepcopy(data)))
        if task=='extract':
            answer=copy.deepcopy(FIXTURE['answer'])
            if data.get('repair_feedback'):
                answer['events'][0]['title']='Thom admits fear of Celia’s robotic hand'
                answer['events'][0]['summary']='Thom admits to Celia that her robotic hand frightens him.'
                answer['events'][0]['why']='Clarifies the conflict between them.'
            return answer
        if task=='review':
            supported=not (self.always_reject or 'own robotic hand' in data['events'][0]['summary'])
            return dict(events=[dict(index=0,supported=supported,reason='Cue 4 identifies Celia as the owner. Correct the ownership.' if not supported else 'Supported.',unsupported_relationships=[])],unsupported_coverage=[])
        raise AssertionError('Unexpected task: '+task)

class EvidenceFixTests(unittest.TestCase):
    def test_recorded_escaped_quote_and_ellipsis_join_are_accepted(self):
        answer=copy.deepcopy(FIXTURE['answer']);group=FIXTURE['cues']
        self.assertTrue(answer['events'][0]['evidence'].startswith(chr(34)))
        self.assertIn('chased...by',answer['events'][0]['evidence'])
        events,_=Pipeline.validate_chunk(answer,group,{'thom','celia'},0)
        self.assertEqual(events[0]['evidence'],' '.join(c['text'] for c in group))
        self.assertEqual(events[0]['evidence_cue_ids'],[4,5,6,7,8])
    def test_invented_words_are_still_rejected(self):
        answer=copy.deepcopy(FIXTURE['answer']);answer['events'][0]['evidence']='I own this robotic hand.'
        with self.assertRaisesRegex(ValueError,'words absent'):Pipeline.validate_chunk(answer,FIXTURE['cues'],{'thom','celia'},0)
    def test_typographic_punctuation_does_not_change_words(self):
        self.assertTrue(quote_is_supported('“I’m afraid…of it!”',"I'm afraid...\nof it."))
        self.assertFalse(quote_is_supported('I am afraid of it.',"I'm afraid...of it."))
        self.assertFalse(quote_is_supported('!!!','words'))
        self.assertFalse(quote_is_supported('not here','here not'))
    def test_ownership_review_causes_one_real_correction_path(self):
        ai=ReviewFixture()
        with tempfile.TemporaryDirectory() as rt:
            events,_=Pipeline(None,ai,rt).extract_reviewed(dict(cast=FIXTURE['cast'],cues=FIXTURE['cues'],following_context=[],previous_context=[]),FIXTURE['cues'],{'thom','celia'},0,lambda *args:None)
        self.assertEqual([c[0] for c in ai.calls],['extract','review','extract','review'])
        self.assertIn('repair_feedback',ai.calls[2][1]);self.assertEqual(ai.invalidated,['extract'])
        self.assertIn('her robotic hand',events[0]['summary']);self.assertEqual(ai.logs[0]['status'],'repair_requested')
    def test_repeated_unsupported_output_is_omitted_without_fallback(self):
        ai=ReviewFixture(always_reject=True)
        with tempfile.TemporaryDirectory() as rt:
            events,spans=Pipeline(None,ai,rt).extract_reviewed(dict(cast=FIXTURE['cast'],cues=FIXTURE['cues']),FIXTURE['cues'],{'thom','celia'},0,lambda *args:None)
        self.assertEqual(events,[]);self.assertTrue(all(s['category']=='unclassified' for s in spans))
        self.assertEqual(len(ai.calls),4);self.assertEqual(ai.logs[-1]['status'],'claims_omitted')
    def test_repair_invalidates_extraction_not_reviewer_cache(self):
        with tempfile.TemporaryDirectory() as rt:
            ai=Nova(rt,key='test-only',interval=0,model='nova-2-lite-v1');extract=Path(rt)/'extract.json';review=Path(rt)/'review.json';extract.write_text('{}');review.write_text('{}')
            ai.local.by_task={'extract':extract,'review':review};ai.local.cache=review
            ai.invalidate('extract');self.assertFalse(extract.exists());self.assertTrue(review.exists())

if __name__=='__main__':unittest.main()
