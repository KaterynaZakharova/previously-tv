/* Optional real-browser QA. The shipping TV app has no npm/browser dependency. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PREVIOUSLY_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const out=process.env.PREVIOUSLY_QA_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'previously-tv-qa-'));
fs.mkdirSync(out,{recursive:true});
const port=process.env.PREVIOUSLY_QA_PORT||'8768';
const server=spawn(process.env.PREVIOUSLY_PYTHON||'python',['server.py','--port',port],{
  cwd:root,env:{...process.env,NOVA_API_KEY:''},
});
let browser;server.stderr.on('data',()=>{});
const ready=new Promise((resolve,reject)=>{
  server.stdout.on('data',d=>{if(d.toString().includes('Previously TV:'))resolve();});
  server.on('error',reject);server.on('exit',code=>{if(code)reject(Error('QA server exited: '+code));});
});
async function shot(page,name){await page.screenshot({path:path.join(out,name+'.png')});}
(async()=>{
  await ready;
  browser=await chromium.launch({...(process.env.PREVIOUSLY_CHROMIUM?{executablePath:process.env.PREVIOUSLY_CHROMIUM}:{}),args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
  const page=await browser.newPage({viewport:{width:1920,height:1080}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+port+'/?tv=1');
  await page.waitForFunction(()=>window.HomeApp?.getState()?.data);
  await page.evaluate(()=>document.fonts.ready);
  assert.equal(await page.locator('#hero-about').evaluate(e=>getComputedStyle(e).fontSize),'28px');
  assert(await page.evaluate(()=>document.fonts.check('500 28px Manrope')));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),1920);
  assert.equal(await page.locator('#hero-play .feather').count(),1);
  assert.equal(await page.locator('[data-row-play] .feather').count(),2);
  await shot(page,'home-1080');
  await page.locator('#hero-details').click();
  assert.equal(await page.locator('.episode-picture .feather').count(),3);
  await shot(page,'details-1080');await page.keyboard.press('Escape');
  await page.locator('#hero-play').click();
  await page.waitForFunction(()=>document.getElementById('feature-video').readyState>=2);
  assert(await page.evaluate(()=>document.fullscreenElement===document.documentElement));
  // Regression: fullscreen top layer must not obscure a previously opened modal.
  assert(await page.evaluate(()=>document.getElementById('feature-player').contains(document.elementFromPoint(innerWidth/2,innerHeight/2))));
  await page.keyboard.press(' ');
  assert.equal(await page.locator('#feature-toggle').getAttribute('aria-label'),'Play');
  assert.equal(await page.locator('#feature-toggle polygon').count(),1);
  await page.keyboard.press(' ');
  assert.equal(await page.locator('#feature-toggle').getAttribute('aria-label'),'Pause');
  await page.keyboard.press('ArrowDown');await shot(page,'player-1080');
  await page.evaluate(()=>window.PreviouslyTV.navigate('up'));
  await page.waitForFunction(()=>document.getElementById('feature-player').classList.contains('chrome-hidden'));
  await page.evaluate(()=>window.PreviouslyTV.navigate('down'));
  assert.equal(await page.locator('#feature-player').evaluate(e=>e.classList.contains('chrome-hidden')),false);
  await page.locator('#feature-previously').click();
  await page.waitForFunction(()=>window.RecapFeed?.getState()?.navigator?.current,null,{timeout:60000});
  await page.waitForFunction(()=>document.getElementById('feed-video').readyState>=2);
  await page.keyboard.press(' ');
  assert.equal(await page.locator('#feed-play').getAttribute('aria-label'),'Play recap');
  assert.equal(await page.locator('#feed-play .control-label').textContent(),'Play');
  const sourceTime=await page.locator('#feature-video').evaluate(e=>e.currentTime);
  await shot(page,'recap-1080');await page.keyboard.press('ArrowRight');
  await page.waitForFunction(()=>document.getElementById('feed-map-graph').querySelectorAll('.feed-person').length>0);
  await shot(page,'story-map-1080');await page.keyboard.press('Escape');await page.keyboard.press('Escape');
  assert(Math.abs(await page.locator('#feature-video').evaluate(e=>e.currentTime)-sourceTime)<.5);
  await page.locator('#feature-close').click();await page.waitForFunction(()=>!document.fullscreenElement);
  const sizes=[];
  for(const [width,height] of [[1280,720],[960,540]]){
    await page.setViewportSize({width,height});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
    const r=await page.locator('#hero-play').boundingBox();assert(r.x>=width*.05&&r.x+r.width<=width*.95);
    sizes.push({width,height,body_css_px:await page.locator('#hero-about').evaluate(e=>parseFloat(getComputedStyle(e).fontSize))});
    await shot(page,'home-'+width);
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({browser:'Chromium',physical_fire_tv:false,body_1080_css_px:28,bundled_font:true,sizes,uncaught_errors:errors,verified:['local Feather hydration','dynamic episode icons','fullscreen top layer','play/pause icon and accessible name','idle controls hide','native bridge reveals controls','real source and recap playback','map overlay','source resume','no horizontal overflow']},null,2)+'\n');
  console.log('PASS: TV design browser checks. Screenshots and report: '+out);
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();server.kill();});
