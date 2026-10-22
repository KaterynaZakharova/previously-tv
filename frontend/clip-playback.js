/* Short recap clips are downloaded locally before playback; bounded, per-player cache. */
(function(root){'use strict';
class ClipPlayback {
  constructor(video, options={}) {
    this.video=video; this.options=options; this.entries=new Map(); this.bytes=0;
    this.maxBytes=options.maxBytes||32*1024*1024;
    this.maxClipBytes=options.maxClipBytes||12*1024*1024;
    this.timeoutMs=options.timeoutMs||45000;
    this.fetch=options.fetch||(typeof fetch==='function'?fetch.bind(globalThis):null);
    this.urls=options.urls||(typeof URL!=='undefined'?URL:null);
    this.abortClass=options.abortClass||(typeof AbortController!=='undefined'?AbortController:null);
    this.supported=!!(this.fetch&&this.urls?.createObjectURL&&this.abortClass&&typeof Blob!=='undefined');
    this.generation=0; this.current=null; this.upcoming=[]; this.wanted=false;
    this.loading=false; this.ready=false; this.background=null; this.lastError=null;this.attempted=new Set();
  }
  state(loading) {this.loading=loading; this.options.onBuffering?.(loading);}
  allowed() {return !this.options.canPlay||this.options.canPlay();}
  drop(url) {
    const entry=this.entries.get(url); if(!entry)return;
    this.entries.delete(url); entry.controller?.abort();
    if(entry.local){this.urls.revokeObjectURL(entry.local); this.bytes-=entry.bytes;}
  }
  trim(reserve=0) {
    for(const [url,entry] of this.entries) {
      if(this.bytes+reserve<=this.maxBytes)break;
      if(url!==this.current&&entry.local)this.drop(url);
    }
  }
  async download(url) {
    let entry=this.entries.get(url);
    if(entry){this.entries.delete(url);this.entries.set(url,entry);return entry.promise;}
    this.trim(this.maxClipBytes);
    entry={controller:new this.abortClass(),bytes:0,local:null};this.entries.set(url,entry);
    entry.promise=(async()=>{
      const timer=setTimeout(()=>entry.controller.abort(),this.timeoutMs);
      try {
        const response=await this.fetch(url,{credentials:'same-origin',cache:'force-cache',signal:entry.controller.signal});
        if(!response.ok)throw Error('The clip could not be downloaded ('+response.status+').');
        const declared=Number(response.headers?.get('Content-Length')||0);
        if(declared>this.maxClipBytes)throw Error('This clip exceeds the TV download limit. Rebuild the recap with the updated server.');
        let blob;
        if(response.body?.getReader) {
          const reader=response.body.getReader(),chunks=[];let size=0;
          try {
            while(true){const {done,value}=await reader.read();if(done)break;
              size+=value.byteLength;
              if(size>this.maxClipBytes){entry.controller.abort();throw Error('This clip exceeds the TV download limit. Rebuild the recap.');}
              chunks.push(value);
            }
          }finally{reader.releaseLock();}
          blob=new Blob(chunks,{type:'video/mp4'});
        }else {
          blob=await response.blob();
          if(blob.size>this.maxClipBytes)throw Error('This clip exceeds the TV download limit. Rebuild the recap.');
        }
        if(!blob.size)throw Error('The downloaded clip is empty.');
        // A fetch can settle just after Back/Next. Never resurrect a cancelled entry.
        if(entry.controller.signal.aborted||this.entries.get(url)!==entry)throw Error('Clip download cancelled.');
        this.trim(blob.size);entry.bytes=blob.size;entry.local=this.urls.createObjectURL(blob);this.bytes+=blob.size;
        return entry.local;
      }catch(error){if(this.entries.get(url)===entry)this.entries.delete(url);throw error;}
      finally{clearTimeout(timer);}
    })();
    return entry.promise;
  }
  load(url, upcoming=[]) {
    const token=++this.generation;this.current=url;this.wanted=true;this.ready=false;this.lastError=null;
    this.upcoming=[...new Set(upcoming.filter(x=>x&&x!==url))].slice(0,2);this.attempted.clear();
    // Current-clip downloads take priority over speculative traffic.
    for(const [key,entry] of this.entries)if(!entry.local&&key!==url)this.drop(key);
    const v=this.video;v.pause();v.removeAttribute('src');v.load();v.preload='auto';
    if(!url){this.state(false);return Promise.resolve();}
    if(!this.supported){v.src=url;v.load();this.ready=true;this.state(false);this.play();return Promise.resolve();}
    this.state(true);
    return this.download(url).then(local=>{
      if(token!==this.generation||url!==this.current)return;
      v.src=local;v.load();this.ready=true;this.state(false);
      if(this.wanted)this.play();this.warm();
    }).catch(error=>{
      if(token!==this.generation)return;
      this.ready=false;this.state(false);this.lastError=error;
      this.options.onError?.('Clip download failed. Press Play to retry, or Back to return. '+error.message);
    });
  }
  prefetch(urls) {
    this.upcoming=[...new Set(urls.filter(x=>x&&x!==this.current))].slice(0,2);
    this.attempted=new Set([...this.attempted].filter(key=>this.upcoming.includes(key)));
    for(const [key,entry] of this.entries)if(!entry.local&&key!==this.current&&!this.upcoming.includes(key))this.drop(key);
    this.warm();
  }
  warm() {
    if(!this.supported||!this.ready||this.background||!this.current)return;
    const url=this.upcoming.find(key=>!this.entries.has(key)&&!this.attempted.has(key));if(!url)return;
    this.attempted.add(url);this.background=url;
    this.download(url).catch(()=>{
      // A speculative failure is retried only when that clip becomes current.
      this.upcoming=this.upcoming.filter(key=>key!==url);
    }).finally(()=>{if(this.background===url)this.background=null;this.warm();});
  }
  play() {
    this.wanted=true;
    if(this.lastError)return this.load(this.current,this.upcoming);
    if(!this.ready||!this.allowed())return Promise.resolve();
    return Promise.resolve(this.video.play()).catch(()=>this.options.onPlayBlocked?.());
  }
  pause() {this.wanted=false;this.video.pause();}
  toggle() {if(this.lastError)return this.play();if(this.loading){this.wanted=!this.wanted;return;}return this.video.paused?this.play():this.pause();}
  clear() {
    ++this.generation;this.current=null;this.wanted=false;this.ready=false;this.upcoming=[];
    this.video.pause();this.video.removeAttribute('src');this.video.load();
    for(const key of [...this.entries.keys()])this.drop(key);
    this.background=null;this.lastError=null;this.state(false);
  }
  stats() {return {mode:this.supported?'local-download':'stream',downloading:this.loading,cachedClips:[...this.entries.values()].filter(e=>e.local).length,cacheBytes:this.bytes};}
}
root.ClipPlayback=ClipPlayback;
if(typeof module!=='undefined')module.exports=ClipPlayback;
})(typeof window!=='undefined'?window:globalThis);
