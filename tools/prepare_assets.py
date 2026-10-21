#!/usr/bin/env python3
"""Download complete source films, verify them, and prepare TV-safe playback assets.
Python 3.9+, FFmpeg/FFprobe. No movie clips are used as Tears of Steel input.
"""
import argparse,concurrent.futures,hashlib,json,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.media_catalog import MediaCatalog,normalize_database,seconds,slug
ROOT=Path(__file__).resolve().parents[1]
MIRRORS={
 'tears_of_steel':'https://media.xiph.org/tearsofsteel/tears_of_steel_1080p.webm',
 'spring':'https://upload.wikimedia.org/wikipedia/commons/a/a5/Spring_-_Blender_Open_Movie.webm',
 'sprite_fright':'https://upload.wikimedia.org/wikipedia/commons/7/76/Sprite_Fright_-_Blender_Open_Movie-full_movie.webm'}

def sha256(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def probe(path):
 run=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_name,codec_type,width,height,nb_frames,r_frame_rate','-of','json',str(path)],capture_output=True,text=True,timeout=45)
 if run.returncode:raise ValueError('FFprobe rejected '+str(path))
 data=json.loads(run.stdout)
 if not any(s['codec_type']=='video' for s in data['streams']):raise ValueError('Source has no video')
 return data

def resolve_original(url):
 if '/w/' not in url:return url
 parts=urlsplit(url);vid=parts.path.split('/w/',1)[1].split('/')[0]
 api=parts.scheme+'://'+parts.netloc+'/api/v1/videos/'+vid
 with urllib.request.urlopen(urllib.request.Request(api,headers={'User-Agent':'Previously/4.0'}),timeout=40) as r:
  if 'json' not in r.headers.get('Content-Type',''):raise ValueError('Source service returned HTML instead of video metadata')
  d=json.load(r)
 files=d.get('files',[])+[f for p in d.get('streamingPlaylists',[]) for f in p.get('files',[])]
 files=[f for f in files if f.get('fileDownloadUrl') or f.get('fileUrl')]
 if not files:raise ValueError('No downloadable original file was listed')
 f=min(files,key=lambda f:abs(f.get('resolution',{}).get('id',720)-720))
 return f.get('fileDownloadUrl') or f['fileUrl']

def download(url,path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.partial')
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Previously/4.0'}),timeout=60) as r:
   kind=r.headers.get('Content-Type','').lower();first=r.read(65536)
   if 'html' in kind or first.lstrip().lower().startswith((b'<!doctype',b'<html')):raise ValueError('Download URL returned an HTML error page, not a movie')
   if not (first.startswith(b'\x1aE\xdf\xa3') or b'ftyp' in first[:40]):raise ValueError('Download did not start with a supported video container')
   expected=int(r.headers.get('Content-Length','0'));count=len(first)
   with tmp.open('wb') as f:
    f.write(first)
    for b in iter(lambda:r.read(1048576),b''):f.write(b);count+=len(b)
   if (expected and count!=expected) or tmp.stat().st_size!=count:raise ValueError('Incomplete download')
  probe(tmp);tmp.replace(path)
 finally:tmp.unlink(missing_ok=True)
 return path

def cut(source,dest,start=None,end=None):
 dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.partial.mp4')
 cmd=['ffmpeg','-nostdin','-v','error','-y']
 if start is not None:cmd+=['-ss',str(start)]
 cmd+=['-i',str(source)]
 if end is not None:cmd+=['-t',str(end-start)]
 cmd+=['-map','0:v:0','-map','0:a:0?','-vf',"scale='min(1280,iw)':-2,setsar=1",'-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-threads','2','-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-movflags','+faststart',str(tmp)]
 try:
  p=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
  if p.returncode:raise ValueError('FFmpeg failed: '+p.stderr[-500:])
  info=probe(tmp);duration=float(info['format']['duration'])
  if end is not None and abs(duration-(end-start))>.2:raise ValueError('Encoded duration %.6f does not match expected %.6f seconds'%(duration,end-start))
  # Actually decode the last frame; container headers alone cannot prove completeness.
  last=subprocess.run(['ffmpeg','-v','error','-sseof','-2','-i',str(tmp),'-frames:v','1','-f','null','-'],capture_output=True,timeout=45)
  if last.returncode:raise ValueError('Ending frames did not decode')
  tmp.replace(dest);return duration
 finally:tmp.unlink(missing_ok=True)

def prepare(root=ROOT,source_dir=None,allow_mirrors=False,verify_only=False):
 root=Path(root).resolve();normalize_database(root);catalog=MediaCatalog(root);media,episodes,_,_,scripts,_,files=catalog.rows();report=[]
 for m in media:
  name=slug(m['title']);folder=root/'assets'/('tv_show' if m['type']=='tv-show' or name=='tears_of_steel' else 'cartoon' if m['type']=='cartoon' else 'movie')/name
  units=[e for e in episodes if e['media_id']==m['id']]
  # Already-prepared inputs require no network access.
  ready=all(e['id'] in files and catalog.path(files[e['id']]['local_path']) for e in units)
  if ready:
   for e in units:
    f=files[e['id']];info=probe(root/f['local_path']);assert abs(float(info['format']['duration'])-f['duration'])<.2
    if sha256(root/f['local_path'])!=f['sha256']:raise ValueError('Playback checksum differs: '+f['local_path'])
    report.append(dict(episode_id=e['id'],path=f['local_path'],duration=f['duration'],verified=True))
   print(m['title'],'already verified',flush=True);continue
  if verify_only:raise ValueError('Playback media missing: '+m['title'])
  cache=Path(source_dir) if source_dir else root/'runtime/downloads';cache.mkdir(parents=True,exist_ok=True);source=cache/(name+'_original.webm');actual=m['url'];note='Downloaded from database URL.'
  if not source.is_file():
   try:actual=resolve_original(m['url']);download(actual,source)
   except Exception as exc:
    if not allow_mirrors:raise RuntimeError(m['title']+': original URL unavailable. '+str(exc)+'. Use --allow-mirrors for the documented CC release mirrors.') from None
    actual=MIRRORS[name];note='Original database URL unavailable; documented CC release mirror used. '+str(exc)
    print(m['title'],note,flush=True);download(actual,source)
  elif source_dir:
   # Explicit local source cache was downloaded separately in this preparation run.
   actual=MIRRORS[name];note='Complete release downloaded from documented mirror after the database URL returned an unavailable page.'
  info=probe(source);duration=float(info['format']['duration']);checksum=sha256(source)
  ending=subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',str(max(0,duration-1.5)),'-i',str(source),'-frames:v','1','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=60)
  if ending.returncode or len(ending.stdout)<100:raise ValueError('Complete source movie has an unreadable or missing ending. Remove the incomplete cached download and retry.')
  with sqlite3.connect(catalog.db) as c:c.execute('INSERT OR REPLACE INTO source_download VALUES (?,?,?,?,?,?)',(m['id'],m['url'],actual,source.stat().st_size,checksum,note))
  for e in units:
   if e['id'] in files and catalog.path(files[e['id']]['local_path']):
    f=files[e['id']];actual_file=root/f['local_path']
    if sha256(actual_file)==f['sha256']:
     report.append(dict(episode_id=e['id'],path=f['local_path'],duration=f['duration'],verified=True));print('Keeping verified',f['local_path'],flush=True);continue
   a=b=None;script=catalog.path(scripts.get(e['id'],{}).get('url'))
   if name=='tears_of_steel':
    if not script:raise ValueError('Episode JSON transcript required for exact boundaries')
    data=json.loads(script.read_text(encoding='utf-8'));a=seconds(data['original_start']);b=seconds(data['original_end'])
    if not 0<=a<b<=duration+.05:raise ValueError('Transcript boundaries fall outside full movie')
    dest=folder/'episodes'/('episode_%02d.mp4'%e['order'])
   else:dest=folder/'movie.mp4'
   print('Preparing',m['title'],e['order'],'source range',a,b,flush=True);length=cut(source,dest,a,b)
   rel=dest.relative_to(root).as_posix();digest=sha256(dest)
   with sqlite3.connect(catalog.db) as c:c.execute('INSERT OR REPLACE INTO playback_asset VALUES (?,?,?,?,?,?,?)',(e['id'],rel,length,a,b,actual,digest))
   report.append(dict(episode_id=e['id'],path=rel,duration=length,original_start=a,original_end=b,sha256=digest));print('Verified',rel,round(length,3),'seconds',flush=True)
 (root/'assets/preparation_report.json').write_text(json.dumps(dict(episodes=report,source_policy='Database URLs first; explicit documented mirrors when enabled. Tears of Steel split exclusively from the complete movie using original_start/original_end.'),indent=2)+'\n')
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--source-dir',type=Path);p.add_argument('--allow-mirrors',action='store_true');p.add_argument('--verify-only',action='store_true');a=p.parse_args()
 try:prepare(a.root,a.source_dir,a.allow_mirrors,a.verify_only)
 except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:print('Preparation failed:',e,file=sys.stderr);raise SystemExit(1)
