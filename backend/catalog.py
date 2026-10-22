import hashlib
import json
import os
import re
from pathlib import Path
from .media_catalog import MediaCatalog,seconds,stamp

ROOT=Path(__file__).resolve().parents[1]

def inside(folder,relative):
    path=(folder/relative).resolve()
    if folder.resolve() not in path.parents or not path.is_file():
        raise ValueError('Missing or unsafe input path: '+str(relative))
    return path

class Catalog:
    def __init__(self,root=ROOT):self.root=Path(root)
    def manifests(self):
        if (self.root/'assets/db.sqlite').is_file():return MediaCatalog(self.root).manifests()
        result=[]
        for p in sorted((self.root/'assets'/'inputs').glob('*/movie.json')):
            try:
                m=json.loads(p.read_text(encoding='utf-8'))
                if not re.fullmatch('[a-z0-9_-]{1,64}',m['id']) or m['id']!=p.parent.name:continue
                m['_folder']=p.parent;result.append(m)
            except (ValueError,KeyError,TypeError):continue
        return result
    def get(self,mid):
        for m in self.manifests():
            if m['id']==mid:return m
        raise KeyError('Unknown movie')
    def inputs(self,m):
        if m.get('catalog_only'):raise ValueError('Catalog entry only. Add movie, timed transcript and cast photos to its folder.')
        folder=m['_folder'];video=inside(folder,m['video'])
        script=inside(folder,m['transcript']) if m.get('transcript') else None
        if not m.get('characters'):raise ValueError('Add a cast list with named character photos.')
        ids=set()
        for c in m['characters']:
            if not re.fullmatch('[a-z0-9_-]{1,64}',c['id']) or c['id'] in ids:raise ValueError('Invalid or duplicate cast ID')
            ids.add(c['id'])
            if c.get('photo'):inside(folder,c['photo'])
        return video,script
    def fingerprint(self,m):
        video,script=self.inputs(m)
        clean={k:v for k,v in m.items() if not k.startswith('_')}
        h=hashlib.sha256(b'previously-analysis-v13-neutral-chronological\0'+json.dumps(clean,sort_keys=True).encode()+(script.read_bytes() if script else b'no-transcript'))
        from .logbook import Logbook
        stored=Logbook(self).load(m)
        if stored:
            h.update((stored['source_key']+stored.get('updated_at',stored['created_at'])).encode())
            return h.hexdigest()[:20]
        model=os.getenv('NOVA_MODEL','auto')
        h.update(('nova\0'+model).encode())
        h.update(str((video.stat().st_size,video.stat().st_mtime_ns)).encode())
        with video.open('rb') as f:
            h.update(f.read(65536));f.seek(max(0,video.stat().st_size-65536));h.update(f.read(65536))
        for c in m['characters']:
            if c.get('photo'):h.update(inside(m['_folder'],c['photo']).read_bytes())
        return h.hexdigest()[:20]
    def cards(self):
        out=[]
        for m in self.manifests():
            card={k:m.get(k) for k in ('id','title','year','synopsis','license','catalog_only')}
            try:
                self.inputs(m);card['inputs_ready']=True;card['input_message']='Movie frames'+(' and timed transcript' if m.get('transcript') else ' · no transcript required')
            except (ValueError,KeyError) as e:card['inputs_ready']=False;card['input_message']=str(e)
            card['poster']='/inputs/'+m['id']+'/'+m['poster'] if m.get('poster') else None
            from .logbook import Logbook
            try:card['logbook_ready']=bool(Logbook(self).load(m)) if card['inputs_ready'] else False
            except (ValueError,KeyError):card['logbook_ready']=False
            out.append(card)
        return out

def read_srt(path,offset=0):
    if Path(path).suffix.lower()=='.json':
        data=json.loads(Path(path).read_text(encoding='utf-8'));result=[]
        origin=seconds(data.get('original_start',0))
        stop=seconds(data['original_end'])-origin if data.get('original_end') else None
        for line in data.get('lines',[]):
            start=seconds(line.get('episode_start',line['start']))-(0 if 'episode_start' in line else origin)+offset
            end=seconds(line.get('episode_end',line['end']))-(0 if 'episode_end' in line else origin)+offset
            start=max(0,start);end=min(end,stop) if stop is not None else end
            if end<=start or not str(line.get('text','')).strip():continue
            result.append(dict(id=len(result)+1,source_cue_id=line.get('id'),start=start,end=end,text=str(line['text']),
                original_start_timecode=stamp(start-offset+origin),original_end_timecode=stamp(end-offset+origin),
                speaker=line.get('speaker'),speaker_character_id=('c'+str(line['character_id'])) if line.get('character_id') else None,
                speaker_confidence=line.get('speaker_confidence','unverified'),speaker_attribution_note=data.get('speaker_attribution','Supplied transcript metadata, not independently verified.')))
        if not result:raise ValueError('Transcript contains no timed dialogue.')
        if any(c['start']<result[i-1]['start'] for i,c in enumerate(result) if i):raise ValueError('Transcript must be time ordered')
        return result
    text=Path(path).read_text(encoding='utf-8-sig').replace('\r\n','\n')
    cues=[]
    def srt_seconds(t):
        a,b,c=t.replace(',', '.').split(':');return int(a)*3600+int(b)*60+float(c)
    for block in re.split(r'\n\s*\n',text.strip()):
        lines=block.splitlines();idx=next((i for i,l in enumerate(lines) if '-->' in l),None)
        if idx is None:continue
        match=re.match(r'(\d\d:\d\d:\d\d[,.]\d+)\s*-->\s*(\d\d:\d\d:\d\d[,.]\d+)',lines[idx])
        if not match:raise ValueError('Invalid subtitle timestamp')
        start,end=(srt_seconds(x)+offset for x in match.groups())
        if start<0 or end<=start:raise ValueError('Invalid subtitle range')
        txt=re.sub('<[^>]+>','', ' '.join(lines[idx+1:])).strip()
        if txt:cues.append(dict(id=len(cues)+1,start=start,end=end,text=txt))
    if not cues:raise ValueError('A timed .srt transcript is required; a plain script cannot define exact cuts.')
    if any(cues[i]['start']<cues[i-1]['start'] for i in range(1,len(cues))):raise ValueError('Transcript must be time ordered')
    return cues
