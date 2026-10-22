/* Deterministic slow-network, lifecycle and bounded-cache regressions. */
const assert=require('node:assert/strict');
const ClipPlayback=require('../frontend/clip-playback.js');
const flush=async(n=8)=>{while(n--)await new Promise(setImmediate)};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
class Video {
 constructor(){this.paused=true;this.currentTime=0;this.src='';this.plays=0;}
 pause(){this.paused=true;}
 play(){this.paused=false;this.plays++;return Promise.resolve();}
 removeAttribute(name){if(name==='src')this.src='';}
 load(){this.currentTime=0;}
}
function fixture(extra={}) {
 const video=new Video(),requests=[],local=new Map(),revoked=[],errors=[],states=[];
 const urls={createObjectURL:blob=>{const url='blob:test-'+(local.size+revoked.length+1);local.set(url,blob);return url},revokeObjectURL:url=>{local.delete(url);revoked.push(url)}};
 const fetch=(url,options)=>{const task=deferred();requests.push({url,options,...task});return task.promise};
 const player=new ClipPlayback(video,{fetch,urls,onError:e=>errors.push(e),onBuffering:v=>states.push(v),...extra});
 const finish=(index,bytes=8)=>requests[index].resolve({ok:true,headers:{get:()=>String(bytes)},blob:async()=>new Blob([new Uint8Array(bytes)],{type:'video/mp4'})});
 return {player,video,requests,local,revoked,errors,states,finish};
}
(async()=>{
 // Wait for the entire body, not just HTTP headers or metadata.
 let f=fixture(),body=deferred(),loading=f.player.load('/a.mp4');
 f.requests[0].resolve({ok:true,headers:{get:()=>8},blob:()=>body.promise});await flush();
 assert.equal(f.video.plays,0);assert.equal(f.video.src,'');assert.equal(f.player.loading,true);
 body.resolve(new Blob([new Uint8Array(8)]));await loading;
 assert.equal(f.video.plays,1);assert(f.video.src.startsWith('blob:'));assert.deepEqual(f.states,[true,false]);
 f.player.clear();assert.equal(f.local.size,0);assert.equal(f.player.bytes,0);

 // Sequential lookahead downloads the next two; switching to a cached clip is local.
 f=fixture();loading=f.player.load('/a.mp4',['/b.mp4','/c.mp4','/never.mp4']);f.finish(0);await loading;await flush();
 assert.deepEqual(f.requests.map(r=>r.url),['/a.mp4','/b.mp4']);assert.equal(f.video.paused,false);
 f.finish(1);await flush();assert.equal(f.requests[2].url,'/c.mp4');f.finish(2);await flush();
 assert.equal(f.requests.length,3);await f.player.load('/b.mp4',['/c.mp4']);assert.equal(f.requests.length,3);
 assert.equal(f.video.plays,2);f.player.clear();

 // A late fetch from a skipped clip cannot replace or start the new clip.
 f=fixture();const a=f.player.load('/a.mp4'),b=f.player.load('/b.mp4');
 assert.equal(f.requests[0].options.signal.aborted,true);f.finish(1);await b;const selected=f.video.src;
 f.finish(0);await a;assert.equal(f.video.src,selected);assert.equal(f.video.plays,1);assert.equal(f.errors.length,0);f.player.clear();

 // Back cancels all work, even if the network ignores abort until its final response.
 f=fixture();loading=f.player.load('/a.mp4');f.player.clear();f.finish(0);await loading;
 assert.equal(f.video.plays,0);assert.equal(f.local.size,0);assert.equal(f.video.src,'');assert.equal(f.errors.length,0);

 // Pause and voice/modal suspension preserve user intent while downloading.
 let allowed=true;f=fixture({canPlay:()=>allowed});loading=f.player.load('/a.mp4');f.player.pause();allowed=false;
 f.finish(0);await loading;assert.equal(f.video.paused,true);assert.equal(f.video.plays,0);
 allowed=true;await f.player.play();assert.equal(f.video.plays,1);f.player.clear();
 f=fixture();loading=f.player.load('/a.mp4');f.player.toggle();f.finish(0);await loading;
 assert.equal(f.video.plays,0);f.player.toggle();assert.equal(f.video.plays,1);f.player.clear();

 // A download failure stays visible and can be retried with Play.
 f=fixture();loading=f.player.load('/a.mp4');f.requests[0].reject(Error('offline'));await loading;
 assert(f.errors[0].includes('Press Play to retry'));assert.equal(f.video.plays,0);
 loading=f.player.toggle();assert.equal(f.requests.length,2);f.finish(1);await loading;
 assert.equal(f.video.plays,1);f.player.clear();

 // Failed speculative fetches never interrupt or report an error over the active clip.
 f=fixture();loading=f.player.load('/a.mp4',['/b.mp4']);f.finish(0);await loading;await flush();
 f.requests[1].reject(Error('offline'));await flush();assert.equal(f.errors.length,0);assert.equal(f.video.paused,false);f.player.clear();

 // Bounded memory must not continually re-download evicted lookahead clips.
 f=fixture({maxBytes:20,maxClipBytes:10});loading=f.player.load('/a.mp4',['/b.mp4','/c.mp4']);f.finish(0,10);await loading;await flush();
 f.finish(1,10);await flush();f.finish(2,10);await flush();assert.equal(f.requests.length,3);
 assert(f.player.bytes<=20);assert.equal(f.local.size,2);assert(f.revoked.length>=1);f.player.clear();

 // Reject unknown/chunked oversized files as well as oversized Content-Length.
 f=fixture({maxClipBytes:10});loading=f.player.load('/huge.mp4');f.finish(0,11);await loading;
 assert.equal(f.video.plays,0);assert.equal(f.local.size,0);assert(f.errors[0].includes('limit'));f.player.clear();
 f=fixture({maxClipBytes:10});loading=f.player.load('/chunked.mp4');f.requests[0].resolve({ok:true,headers:{get:()=>null},
  body:new ReadableStream({start(c){c.enqueue(new Uint8Array(11));c.close();}})});await loading;
 assert.equal(f.video.plays,0);assert.equal(f.requests[0].options.signal.aborted,true);f.player.clear();

 // New current requests abort speculative traffic, not a promoted matching fetch.
 f=fixture();loading=f.player.load('/a.mp4',['/b.mp4']);f.finish(0);await loading;await flush();
 loading=f.player.load('/b.mp4');assert.equal(f.requests.length,2);assert.equal(f.requests[1].options.signal.aborted,false);
 f.finish(1);await loading;assert.equal(f.video.plays,2);f.player.clear();
 console.log('PASS: full-body startup, two-clip prefetch, cached switch, skip/Back cancellation, pause and voice intent, retries, quiet speculative failures, bounded cache, oversize protection and in-flight promotion.');
})().catch(error=>{console.error(error);process.exit(1)});
