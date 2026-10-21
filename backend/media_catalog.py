"""SQLite/asset adapter. Source metadata stays separate from per-viewer progress."""
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote,unquote


def seconds(value):
    if isinstance(value,(int,float)):return float(value)
    parts=str(value).replace(',','.').split(':')
    if len(parts)==3:return int(parts[0])*3600+int(parts[1])*60+float(parts[2])
    return float(value)


def stamp(value):
    ms=round(float(value)*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return '%02d:%02d:%02d.%03d'%(h,m,s,ms)


def slug(title):return re.sub(r'[^a-z0-9]+','_',title.lower()).strip('_')

class MediaCatalog:
    def __init__(self,root):self.root=Path(root);self.db=self.root/'assets/db.sqlite'
    def connection(self):
        c=sqlite3.connect('file:'+self.db.resolve().as_posix()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
    def path(self,value):
        if not isinstance(value,str) or not value.startswith('assets/'):return None
        p=(self.root/value).resolve();base=(self.root/'assets').resolve()
        return p if base in p.parents and p.is_file() else None
    def public(self,value):return '/'+quote(value,safe='/') if self.path(value) else None
    def rows(self):
        with self.connection() as c:
            media=[dict(r) for r in c.execute('SELECT * FROM media ORDER BY id')]
            episodes=[dict(r) for r in c.execute('SELECT * FROM episode ORDER BY media_id,"order"')]
            people=[dict(r) for r in c.execute('SELECT * FROM "character" ORDER BY id')]
            actors={r['id']:dict(r) for r in c.execute('SELECT * FROM "cast"')}
            transcripts={r['episode_id']:dict(r) for r in c.execute('SELECT * FROM transcript')}
            relations=[dict(r) for r in c.execute('SELECT * FROM relationship ORDER BY id')]
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            files={r['episode_id']:dict(r) for r in c.execute('SELECT * FROM playback_asset')} if 'playback_asset' in tables else {}
        return media,episodes,people,actors,transcripts,relations,files
    def library(self):
        media,episodes,people,actors,scripts,relations,files=self.rows();out=[]
        browse_file=self.root/'assets/catalog_browse.json'
        browse=json.loads(browse_file.read_text(encoding='utf-8')) if browse_file.is_file() else {}
        for m in media:
            name=slug(m['title']);eps=[]
            for e in [x for x in episodes if x['media_id']==m['id']]:
                script=scripts.get(e['id'],{}).get('url');source=self.path(script)
                data=json.loads(source.read_text(encoding='utf-8')) if source else {}
                f=files.get(e['id'],{});a=seconds(data.get('original_start',0));b=seconds(data['original_end']) if data.get('original_end') else None
                unit=name+'_e%02d'%e['order'];url=self.public(f.get('local_path'))
                eps.append(dict(id=e['id'],media_id=m['id'],unit_id=unit,order=e['order'],title=e.get('title') or m['title'],about=e.get('about') or '',
                    preview=self.public(e.get('preview_url')) or self.public(m.get('preview_url')),video=url,ready=bool(url),
                    duration=f.get('duration'),original_start=a,original_end=b,transcript=script if source else None,
                    recap_ready=bool(url),recap_note=None if source else 'Visual action analysis · no transcript supplied.'))
            cast=[]
            for p in [x for x in people if x['media_id']==m['id']]:
                actor=actors.get(p['actor_id']);raw=json.loads(p.get('presence') or '{}')
                portrait=self.public(p.get('picture_url'))
                if not portrait:
                    parent=self.path(m.get('preview_url'));folder=parent.parent.parent if parent else None
                    if folder:
                        wanted=re.sub(r'^the_','',slug(p['name']))
                        candidates=[f for f in (folder/'characters').glob('*') if f.is_file() and f.suffix.lower() in ('.jpg','.png','.webp','.jpeg') and re.sub(r'^the_','',re.sub(r'^\d+_','',slug(f.stem)))==wanted]
                        if len(candidates)==1:portrait=self.public(candidates[0].relative_to(self.root).as_posix())
                cast.append(dict(id=p['id'],name=p['name'],bio=p.get('bio') or '',photo=portrait,
                    actor=dict(name=' '.join(x for x in (actor['name'],actor.get('surname')) if x),bio=actor.get('bio'),photo=self.public(actor.get('picture_url'))) if actor else None,presence=raw))
            out.append(dict(id=m['id'],slug=name,title=m['title'],browse_about=browse.get(name,''),about=m.get('about') or '',type='series' if len(eps)>1 else m['type'],
                source_type=m['type'],source_url=m.get('url'),license=m.get('license'),preview=self.public(m.get('preview_url')),
                episodes=eps,characters=cast,relationships=[r for r in relations if r['character_from'] in {p['id'] for p in cast} and r['character_to'] in {p['id'] for p in cast}]))
        return out
    def manifests(self):
        result=[];raw=self.rows();by_person={r['id']:r for r in raw[2]}
        for m in self.library():
            for e in m['episodes']:
                chars=[]
                for p in m['characters']:
                    original=by_person[p['id']];photo=unquote(p['photo'].lstrip('/')) if p.get('photo') else None
                    chars.append(dict(id='c'+str(p['id']),name=p['name'],photo=photo,actor=p['actor']['name'] if p['actor'] else '',identity_hint=p['bio'],metadata_presence=p['presence']))
                with self.connection() as c:
                    row=c.execute('SELECT local_path FROM playback_asset WHERE episode_id=?',(e['id'],)).fetchone() if raw[6] else None
                license_version='3.0' if '3.0' in (m['license'] or '') else '4.0'
                result.append(dict(id=e['unit_id'],title=m['title']+(' · Episode '+str(e['order']) if len(m['episodes'])>1 else ''),year={'tears_of_steel':2012,'spring':2019,'sprite_fright':2021}.get(m['slug']),
                    synopsis=e['about'],media_id=m['id'],episode_id=e['id'],original_start_seconds=e['original_start'],original_end_seconds=e['original_end'],
                    video=row['local_path'] if row else 'assets/'+m['slug']+'/missing.mp4',transcript=e['transcript'],poster=(e['preview'] or m['preview'] or '').lstrip('/'),characters=chars,
                    license=dict(name=m['license'],short=m['license'],url='https://creativecommons.org/licenses/by/'+license_version+'/',source_url=m['source_url'],
                        attribution=('Tears of Steel (2012), Blender Foundation | mango.blender.org' if m['slug']=='tears_of_steel' else m['title']+', Blender Studio / Blender Foundation'),note='Local playback transcode; episode cuts follow the supplied transcript boundaries.'),
                    supplied_relationships=[r for r in m['relationships'] if seconds(r['timestamp_from'])<(e['original_end'] or float('inf')) and seconds(r['timestamp_to'])>e['original_start']],_folder=self.root))
        return result
    def public_asset(self,name):
        # Database and transcript documents are deliberately not generally public.
        p=self.path(name)
        if not p or p.suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.mp4'):raise KeyError('Unknown asset')
        return p


def normalize_database(root):
    """Resolve supplied file names; do not change source film URLs or transcript times."""
    root=Path(root);db=root/'assets/db.sqlite'
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE IF NOT EXISTS playback_asset (episode_id INTEGER PRIMARY KEY REFERENCES episode(id),local_path TEXT NOT NULL,duration REAL NOT NULL,original_start REAL,original_end REAL,download_url TEXT,sha256 TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS source_download (media_id INTEGER PRIMARY KEY REFERENCES media(id),requested_url TEXT,download_url TEXT,source_bytes INTEGER,sha256 TEXT,note TEXT)')
        for m in c.execute('SELECT * FROM media').fetchall():
            folder=root/'assets'/('tv_show' if m['type']=='tv-show' or m['title']=='Tears of Steel' else 'cartoon' if m['type']=='cartoon' else 'movie')/slug(m['title'])
            previews=folder/('previews' if (folder/'previews').is_dir() else 'preview')
            image=previews/'media_01.jpg'
            if image.is_file():c.execute('UPDATE media SET preview_url=? WHERE id=?',(image.relative_to(root).as_posix(),m['id']))
            for e in c.execute('SELECT * FROM episode WHERE media_id=?',(m['id'],)).fetchall():
                ep=previews/('episode_%02d.jpg'%e['order']);ep=ep if ep.is_file() else image
                if ep.is_file():c.execute('UPDATE episode SET preview_url=? WHERE id=?',(ep.relative_to(root).as_posix(),e['id']))
            files={int(p.name.split('_',1)[0]):p for p in (folder/'characters').glob('*.jpg') if p.name.split('_',1)[0].isdigit()}
            if m['title']=='Tears of Steel':
                for character in c.execute('SELECT id FROM "character" WHERE media_id=?',(m['id'],)).fetchall():
                    photo=files.get(character['id'])
                    if photo:c.execute('UPDATE "character" SET picture_url=? WHERE id=?',(photo.relative_to(root).as_posix(),character['id']))
                extra=folder/'characters/11_scientist_warior.jpg'
                if extra.is_file() and not c.execute('SELECT 1 FROM "character" WHERE media_id=? AND picture_url=?',(m['id'],extra.relative_to(root).as_posix())).fetchone():
                    c.execute('INSERT INTO "character" (actor_id,media_id,name,bio,picture_url,presence) VALUES (?,?,?,?,?,?)',(0,m['id'],'Scientist warrior','Additional supplied character portrait; role and relationships are not described in the supplied database.',extra.relative_to(root).as_posix(),'{}'))
