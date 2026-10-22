"""Voice chat uses watched evidence and viewer-owned sessions; no live model calls."""
import base64
import copy
import http.cookiejar
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from types import SimpleNamespace
from unittest.mock import patch

from backend.ai import Nova, AIResponseInvalid, AIUnavailable
from backend.chat import Chat
from backend.voice import Speech
from server import create_server


class EvidenceAI:
    key = 'fixture-only';provider = 'nova';label = 'Controlled test Nova';model = 'fixture'
    def __init__(self): self.calls=[];self.logs=[];self.invalidations=[];self.bad=False
    def log(self, row): self.logs.append(row)
    def invalidate(self, task): self.invalidations.append(task)
    def json(self, task, instruction, data, schema, **kwargs):
        self.calls.append(copy.deepcopy(data))
        greeting=data.get('greeting')
        answer=dict(answer=('Hello. Ask me about '+data['title']+'.') if greeting else 'The watched scene establishes a meeting.',
            evidence_ids=[] if greeting or self.bad else [data['evidence'][0]['id']] if data['evidence'] else [],
            insufficient_evidence=not bool(data['evidence']) and not greeting,finish=data.get('question')=='finish and return')
        return Nova.validate_schema(answer,schema)


class VoiceChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.ai=EvidenceAI()
        self.episodes=[dict(id=i,unit_id='story_e0'+str(i),order=i,duration=100.,original_start=(i-1)*100.,
            original_end=i*100.,transcript='e%d.srt'%i) for i in (1,2,3)]
        for i in (1,2,3):
            (self.root/('e%d.srt'%i)).write_text('1\n00:00:04,000 --> 00:00:09,000\nEarly meeting\n\n2\n00:00:31,000 --> 00:00:33,000\nLATER_SECRET\n\n3\n00:01:25,000 --> 00:01:30,000\nSKIPPED_SECRET\n')
        self.media=dict(id=1,title='A test story',episodes=self.episodes)
        spans={1:[[0,20],[35,80]],2:[[0,18],[30,35]],3:[[0,100]]}
        catalog=SimpleNamespace(library=lambda:[self.media],path=lambda p:self.root/p)
        self.home=SimpleNamespace(catalog=catalog,episode_ranges=lambda sid,m,e:spans[e['id']],
            story_map=lambda sid,mid,at:dict(characters=[dict(id='c1',name='Seen Person')],relationships=[]))
        events=[dict(id='past',title='Watched action',summary='A visible meeting.',ranges=[dict(start=10,end=15)]),
                dict(id='bad',title='SKIPPED_ACTION_SECRET',summary='Not watched.',ranges=[dict(start=10,end=15),dict(start=85,end=90)])]
        self.service=SimpleNamespace(ai=self.ai,catalog=SimpleNamespace(get=lambda unit:dict(id=unit)),analysis=lambda m:dict(events=events))
        self.chat=Chat(self.service,self.home)
        self.context=dict(mode='recap',media_id=1,episode_id=2,source_time=20.)

    def tearDown(self): self.tmp.cleanup()
    def start(self): return self.chat.start('alice',self.context)

    def test_greeting_is_a_real_model_turn_and_followups_use_bounded_evidence(self):
        result=self.start();token=result['session_id']
        self.assertTrue(self.ai.calls[0]['greeting']);self.assertEqual(self.ai.calls[0]['evidence'],[])
        reply=self.chat.ask('alice',token,'Who was at the meeting?')
        self.assertTrue(reply['message']['citations'])
        sent=self.ai.calls[-1]
        text=json.dumps(sent)
        self.assertNotIn('SECRET',text)
        self.assertTrue(all(r['end']<=120 for r in sent['evidence']))
        self.chat.ask('alice',token,'What does that explain?')
        self.assertEqual(self.ai.calls[-1]['conversation'][0]['question'],'Who was at the meeting?')

    def test_watched_future_scene_and_other_episodes_do_not_leak_into_earlier_context(self):
        result=self.start();self.chat.ask('alice',result['session_id'],'Explain the story')
        self.assertFalse(any('story_e03' in r['id'] for r in self.ai.calls[-1]['evidence']))
        self.assertFalse(any('story_e02:2' in r['id'] for r in self.ai.calls[-1]['evidence']))
        self.assertFalse(any('story_e01:2' in r['id'] for r in self.ai.calls[-1]['evidence']))

    def test_cross_viewer_and_expired_sessions_are_rejected(self):
        token=self.start()['session_id']
        with self.assertRaises(KeyError): self.chat.ask('bob',token,'Who?')
        with self.assertRaises(KeyError): self.chat.speech_text('bob',token,'x')
        self.chat.sessions[token]['updated']-=1900
        with self.assertRaises(KeyError): self.chat.ask('alice',token,'Who?')

    def test_finish_removes_history_and_speech_access(self):
        result=self.start();token=result['session_id'];mid=result['message']['id']
        self.assertEqual(self.chat.speech_text('alice',token,mid),result['message']['text'])
        self.assertTrue(self.chat.ask('alice',token,'finish and return')['finish'])
        self.assertTrue(self.chat.finish('alice',token)['finished'])
        with self.assertRaises(KeyError):self.chat.speech_text('alice',token,mid)

    def test_no_key_reports_unavailable_not_a_fake_greeting(self):
        self.ai.key=''
        with self.assertRaises(AIUnavailable): self.start()
        self.assertEqual(self.ai.calls,[])

    def test_missing_evidence_citations_are_repaired_then_rejected(self):
        token=self.start()['session_id'];self.ai.bad=True
        with self.assertRaises(AIResponseInvalid):self.chat.ask('alice',token,'What happened?')
        self.assertEqual(len(self.ai.invalidations),2)

    def test_invalid_context_and_parallel_question_are_rejected(self):
        for at in (-1,101,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.chat.start('alice',dict(self.context,source_time=at))
        token=self.start()['session_id'];session=self.chat.sessions[token]
        session['lock'].acquire()
        try:
            with self.assertRaisesRegex(ValueError,'Wait'):self.chat.ask('alice',token,'What?')
        finally:session['lock'].release()

    def test_session_history_is_bounded(self):
        token=self.start()['session_id']
        for _ in range(18):self.chat.ask('alice',token,'Meeting?')
        session=self.chat.sessions[token]
        self.assertEqual(len(session['turns']),8);self.assertEqual(len(session['messages']),14)


class SpeechTests(unittest.TestCase):
    def setUp(self):self.ai=EvidenceAI();self.speech=Speech(self.ai)
    @staticmethod
    def wav(seconds=1):
        output=io.BytesIO()
        with wave.open(output,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x01\x00'*int(16000*seconds))
        return base64.b64encode(output.getvalue()).decode()
    def test_default_local_mode_needs_no_aws_speech_sdk(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(self.speech.capabilities()['output'],'device')
            self.assertFalse(self.speech.capabilities()['cloud_input'])
            with self.assertRaises(AIUnavailable):self.speech.transcribe(self.wav())
    def test_polly_returns_audio_and_never_logs_credentials_or_audio_bytes(self):
        class Polly:
            def synthesize_speech(self,**kwargs):
                self.request=kwargs;return dict(AudioStream=io.BytesIO(b'mp3-fixture'))
        client=Polly()
        with patch.dict(os.environ,{'PREVIOUSLY_VOICE_OUTPUT':'polly'}),patch.object(self.speech,'client',return_value=client):
            result=self.speech.synthesize('A visible meeting.')
        self.assertEqual(base64.b64decode(result['audio_base64']),b'mp3-fixture')
        self.assertEqual(client.request['TextType'],'text')
        self.assertNotIn('mp3-fixture',json.dumps(self.ai.logs))
    def test_transcribe_roundtrip_cleans_private_audio_and_job(self):
        class S3:
            def __init__(self):self.deleted=False
            def put_object(self,**kwargs):self.put=kwargs
            def get_object(self,**kwargs):return dict(Body=io.BytesIO(json.dumps({'results':{'transcripts':[{'transcript':'Why the meeting?'}]}}).encode()))
            def delete_objects(self,**kwargs):self.deleted=True
        class Transcribe:
            def start_transcription_job(self,**kwargs):self.start=kwargs
            def get_transcription_job(self,**kwargs):return dict(TranscriptionJob=dict(TranscriptionJobStatus='COMPLETED'))
            def delete_transcription_job(self,**kwargs):self.deleted=True
        s3,transcribe=S3(),Transcribe()
        with patch.dict(os.environ,{'PREVIOUSLY_RUN_MODE':'aws','AWS_TRANSCRIBE_BUCKET':'private-bucket'}),patch.object(self.speech,'client',side_effect=lambda n:s3 if n=='s3' else transcribe):
            self.assertEqual(self.speech.transcribe(self.wav())['text'],'Why the meeting?')
        self.assertTrue(s3.deleted);self.assertTrue(transcribe.deleted)
        self.assertEqual(transcribe.start['MediaSampleRateHertz'],16000)
        self.assertEqual(s3.put['ServerSideEncryption'],'AES256')
        self.assertNotIn('audio_base64',json.dumps(self.ai.logs))
    def test_invalid_and_oversized_audio_rejected_before_aws_client(self):
        with patch.dict(os.environ,{'PREVIOUSLY_RUN_MODE':'aws','AWS_TRANSCRIBE_BUCKET':'private-bucket'}),patch.object(self.speech,'client') as client:
            for data in ('not-base64',self.wav(21)):
                with self.assertRaises(ValueError):self.speech.transcribe(data)
            client.assert_not_called()


class ChatHTTPTests(unittest.TestCase):
    def test_chat_endpoints_use_cookie_ownership_and_origin_checks(self):
        project=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);shutil.copytree(project/'assets',root/'assets');(root/'frontend').mkdir()
            server=create_server(port=0,root=root,ai=EvidenceAI());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            alice=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            bob=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def post(client,action,data,origin=None):
                req=urllib.request.Request(base+'/api/chat/'+action,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})})
                with client.open(req) as response:return json.load(response)
            try:
                result=post(alice,'start',dict(context=dict(mode='watch',media_id=1,episode_id=2,source_time=0)))
                token=result['session_id'];self.assertEqual(result['speech']['output'],'device')
                with self.assertRaises(urllib.error.HTTPError) as error:post(bob,'ask',dict(session_id=token,question='Who?'))
                self.assertEqual(error.exception.code,404)
                with self.assertRaises(urllib.error.HTTPError) as error:post(alice,'ask',dict(session_id=token,question='Who?'),origin='http://untrusted.example')
                self.assertEqual(error.exception.code,403)
                reply=post(alice,'ask',dict(session_id=token,question='What happened?'));self.assertTrue(reply['message']['citations'])
                self.assertTrue(post(alice,'finish',dict(session_id=token))['finished'])
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
