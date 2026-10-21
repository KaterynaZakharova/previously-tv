/* Presentation only: keep the original programme unobstructed during playback. */
(()=>{'use strict';
const player=document.getElementById('feature-player'),video=document.getElementById('feature-video');
let timer;
function reveal(){
  player.classList.remove('chrome-hidden');clearTimeout(timer);
  if(!player.open||video.paused)return;
  timer=setTimeout(()=>{
    const focused=document.activeElement;
    const focusInControls=player.querySelector('.feature-top')?.contains(focused)||player.querySelector('.feature-bottom')?.contains(focused);
    if(player.open&&!video.paused&&!focusInControls&&document.getElementById('feature-ended').hidden)player.classList.add('chrome-hidden');
  },4500);
}
video.addEventListener('playing',reveal);video.addEventListener('pause',reveal);video.addEventListener('waiting',reveal);
player.addEventListener('pointermove',reveal);player.addEventListener('pointerdown',reveal);player.addEventListener('focusin',reveal);
document.addEventListener('keydown',()=>{if(player.open&&!window.RecapFeed?.getState()?.active)reveal();});
new MutationObserver(()=>{if(player.open)reveal();else clearTimeout(timer);}).observe(player,{attributes:true,attributeFilter:['open']});
const bridge=window.PreviouslyTV;
if(bridge)for(const key of ['navigate','activate','togglePlayback','next','previous','menu']){
  const original=bridge[key];if(typeof original!=='function')continue;
  bridge[key]=function(...args){const result=original.apply(this,args);if(player.open&&!window.RecapFeed?.getState()?.active)reveal();return result;};
}
})();
