import unittest
from backend.logbook_review import validate

class CaptionReviewTests(unittest.TestCase):
    def row(self):return dict(ranges=[dict(start=10,end=24)],visual_evidence='Controlled fixture observation',frame_times=[12,20],cue_ids=[1])
    def test_preserves_only_exact_retained_visual_dialogue_evidence(self):
        r=self.row();self.assertEqual(validate(r,dict(ranges=[dict(start=10,end=25)]),[12,20],[dict(id=1,start=13,end=15)]),r)
    def test_rejects_new_footage_cut_out_evidence_short_and_long_edits(self):
        for changes in (dict(ranges=[dict(start=9,end=24)]),dict(ranges=[dict(start=10,end=14)]),dict(ranges=[dict(start=10,end=24)],frame_times=[22]),dict(cue_ids=[2])):
            with self.assertRaises(ValueError):validate(dict(self.row(),**changes),dict(ranges=[dict(start=10,end=25)]),[12,20],[dict(id=1,start=13,end=15)])

if __name__=='__main__':unittest.main()
