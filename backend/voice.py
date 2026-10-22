"""Optional AWS Polly output and Transcribe input; no credentials on the TV."""
import base64
import io
import json
import os
import time
import uuid
import wave
from .ai import AIUnavailable


class Speech:
    def __init__(self, ai):
        self.ai = ai

    def capabilities(self):
        aws = os.getenv('PREVIOUSLY_RUN_MODE', 'local') == 'aws'
        output = os.getenv('PREVIOUSLY_VOICE_OUTPUT', 'polly' if aws else 'device').lower()
        if output not in ('device', 'polly'):
            raise ValueError('PREVIOUSLY_VOICE_OUTPUT must be device or polly')
        return dict(output=output, cloud_input=bool(aws and os.getenv('AWS_TRANSCRIBE_BUCKET')),
                    language=os.getenv('PREVIOUSLY_VOICE_LANGUAGE', 'en-US'), max_record_seconds=20)

    @staticmethod
    def client(name):
        try:
            import boto3
            from botocore.config import Config
            return boto3.client(name, region_name=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1',
                config=Config(connect_timeout=10, read_timeout=30, retries={'total_max_attempts': 2}))
        except Exception:
            raise AIUnavailable('AWS speech unavailable. Check optional packages, profile and region.') from None

    def synthesize(self, text):
        if self.capabilities()['output'] != 'polly':
            raise AIUnavailable('Use device speech output in this run mode')
        try:
            response = self.client('polly').synthesize_speech(Text=text, TextType='text', OutputFormat='mp3',
                VoiceId=os.getenv('AWS_POLLY_VOICE_ID', 'Joanna'), Engine=os.getenv('AWS_POLLY_ENGINE', 'neural'))
            stream = response['AudioStream']
            try:
                audio = stream.read()
            finally:
                stream.close()
            self.ai.log(dict(task='voice_synthesis', status='ready', provider_service='Amazon Polly', characters=len(text)))
            return dict(audio_base64=base64.b64encode(audio).decode(), mime='audio/mpeg')
        except AIUnavailable:
            raise
        except Exception as error:
            self.ai.log(dict(task='voice_synthesis', status='failed', error_type=type(error).__name__))
            raise AIUnavailable('Voice output unavailable. Check Amazon Polly access/voice/region; the text answer is still available.') from None

    def transcribe(self, encoded):
        if not self.capabilities()['cloud_input']:
            raise AIUnavailable('Cloud microphone input unavailable. Configure AWS_TRANSCRIBE_BUCKET or use device speech recognition.')
        if not isinstance(encoded, str) or len(encoded) > 900000:
            raise ValueError('Voice recording is too large')
        try:
            audio = base64.b64decode(encoded, validate=True)
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000 or not 0 < wav.getnframes() <= 320000:
                    raise ValueError('Use at most 20 seconds of 16 kHz mono PCM WAV')
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                    raise ValueError('Incomplete WAV recording')
        except (wave.Error, EOFError, ValueError):
            raise ValueError('Use at most 20 seconds of 16 kHz mono PCM WAV') from None
        token = 'previously-voice-' + uuid.uuid4().hex
        bucket = os.environ['AWS_TRANSCRIBE_BUCKET']
        prefix = os.getenv('AWS_TRANSCRIBE_PREFIX', 'previously/voice').strip('/')
        key, output = prefix + '/' + token + '.wav', prefix + '/' + token + '.json'
        s3, transcribe = self.client('s3'), self.client('transcribe')
        started = False
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=audio, ContentType='audio/wav', ServerSideEncryption='AES256')
            transcribe.start_transcription_job(TranscriptionJobName=token, MediaFormat='wav', MediaSampleRateHertz=16000,
                LanguageCode=self.capabilities()['language'], Media={'MediaFileUri': 's3://' + bucket + '/' + key},
                OutputBucketName=bucket, OutputKey=output)
            started = True
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline:
                result = transcribe.get_transcription_job(TranscriptionJobName=token)['TranscriptionJob']
                status = result['TranscriptionJobStatus']
                if status == 'FAILED':
                    raise AIUnavailable('Speech recognition failed. Try a shorter, clearer question.')
                if status == 'COMPLETED':
                    body = s3.get_object(Bucket=bucket, Key=output)['Body']
                    try:
                        document = json.loads(body.read())
                    finally:
                        body.close()
                    text = ' '.join(x['transcript'] for x in document['results']['transcripts']).strip()
                    if not text:
                        raise AIUnavailable('No speech heard. Try again or type your question.')
                    self.ai.log(dict(task='voice_transcribe', status='ready', provider_service='Amazon Transcribe', transcript=text))
                    return dict(text=text)
                time.sleep(1)
            raise AIUnavailable('Speech recognition timed out. Try again.')
        except (AIUnavailable, ValueError):
            raise
        except Exception as error:
            self.ai.log(dict(task='voice_transcribe', status='failed', error_type=type(error).__name__))
            raise AIUnavailable('Speech recognition unavailable. Check Transcribe/S3 access and bucket region.') from None
        finally:
            # Configure an S3 lifecycle rule too: timed-out jobs may finish after cleanup.
            try:
                if started:
                    transcribe.delete_transcription_job(TranscriptionJobName=token)
                s3.delete_objects(Bucket=bucket, Delete={'Objects': [{'Key': key}, {'Key': output}], 'Quiet': True})
            except Exception:
                self.ai.log(dict(task='voice_cleanup', status='retry_via_bucket_lifecycle', job=token))
