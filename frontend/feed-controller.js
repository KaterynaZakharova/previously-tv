/* Pure navigation state; elapsed exposure excludes pauses, buffering and overlays. */
(function(root){'use strict';
class RecapNavigator{
 constructor(now=()=>Date.now(),minimumMs=2000,forgetMs=7000){this.now=now;this.minimumMs=minimumMs;this.forgetMs=forgetMs;this.dwellSent=false;this.current=null;this.history=[];this.forward=[];this.pending=[];this.exposure=0;this.since=null;}
 key(item){return item.unit_id+'|'+item.event_id;}
 startExposure(){if(this.current&&this.since===null)this.since=this.now();}
 stopExposure(){if(this.since!==null){this.exposure+=Math.max(0,this.now()-this.since);this.since=null;}}
 elapsed(){return this.exposure+(this.since===null?0:Math.max(0,this.now()-this.since));}
 show(item){this.stopExposure();this.current=item||null;this.exposure=0;this.since=null;this.dwellSent=false;return this.current;}
 sync(unit,items,priority=false){const blocked=new Set([...this.history,...this.forward,...(this.current?[this.current]:[])].map(x=>this.key(x)));const fresh=items.filter(x=>!blocked.has(this.key(x)));const index=this.pending.findIndex(x=>x.unit_id===unit);const other=this.pending.filter(x=>x.unit_id!==unit);if(priority)this.pending=[...fresh,...other];else{const at=index<0?other.length:Math.min(index,other.length);this.pending=[...other.slice(0,at),...fresh,...other.slice(at)];}if(!this.current)this.show(this.forward.shift()||this.pending.shift());return this.current;}
 markForgotten(){this.dwellSent=true;}
 takeDwell(){if(!this.current||this.dwellSent||this.elapsed()<this.forgetMs)return null;this.dwellSent=true;return this.current;}
 down(){if(!this.current)return {rate:null,next:null};const leaving=this.current;const forget=this.takeDwell(),elapsed=this.elapsed(),remember=elapsed>=this.minimumMs&&elapsed<this.forgetMs;this.history.push(leaving);this.show(this.forward.shift()||this.pending.shift());return {rate:remember?leaving:null,forget,next:this.current};}
 up(){if(!this.history.length)return null;if(this.current)this.forward.unshift(this.current);return this.show(this.history.pop());}
}
root.RecapNavigator=RecapNavigator;if(typeof module!=='undefined')module.exports=RecapNavigator;
})(typeof window!=='undefined'?window:globalThis);
