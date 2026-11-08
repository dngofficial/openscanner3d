// Phone control integration with simulated sensors/camera. No hardware or model claims.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const guide=await import(pathToFileURL(__dirname+'/guidance.mjs'));
 const html=fs.readFileSync(__dirname+'/phone.html','utf8');
 const htmlIds=Array.from(html.matchAll(/\bid="([^"]+)"/g),m=>m[1]);
 assert.equal(new Set(htmlIds).size,htmlIds.length,'HTML IDs must be unique.');
 const source=fs.readFileSync(__dirname+'/phone.mjs','utf8').replace(/^import .*\n/,'const {cameraDirection,wrap180,nextStep,isSteady,exposureProblem,drawCoverage}=guide;\n');
 const plan={sectors:12,rings:[{name:'low',min:-15,max:25,target:1},{name:'mid',min:25,max:50,target:1},{name:'high',min:50,max:86,target:1}],min_separation:10};
 let now=0,stops=0,uploads=0,finishes=0,creations=0,denied=false,holdUpload=null,permissionCalls=[];
 const events={},docEvents={},nodes={},storage=new Map();
 const context=new Proxy({getImageData:()=>({data:new Uint8Array(48*36*4).fill(100)})},{get:(o,k)=>o[k]||(()=>{})});
 class Element{
  constructor(){this.style={};this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};this.readyState=2;this.videoWidth=2160;this.videoHeight=3840;}
  getContext(){return context;}toBlob(fn){fn({encoded:true});}setAttribute(k,v){this[k]=v;}
  async play(){}addEventListener(){}
 }
 const el=id=>{assert(htmlIds.includes(id),'Missing HTML element: '+id);return nodes[id]??=new Element();};
 const state={id:'a'.repeat(32),photos:[],coverage:null,options:{max_views:4,turntable:true},job:{state:'idle',message:'Waiting'}};
 function coverage(){const counts=plan.rings.map(()=>Array(12).fill(0));for(const p of state.photos)if(p.ring!=null)counts[p.ring][p.sector]++;return{counts,have:counts.flat().filter(Boolean).length,wanted:36};}
 function snapshot(){state.coverage=coverage();return structuredClone(state);}
 async function fetch(path,options={}){
  const url=new URL(path,'https://localhost');let body;
  if(url.pathname==='/api/plan')body=plan;
  else if(url.pathname==='/api/scans'&&options.method==='POST'){creations++;body=snapshot();}
  else if(url.pathname.endsWith('/phone-mode')){state.options.turntable=false;body=snapshot();}
  else if(options.method==='DELETE'){state.photos.pop();body=snapshot();}
  else if(url.pathname.endsWith('/photos')){
   uploads++;if(holdUpload)await new Promise(r=>holdUpload=r);
   const azimuth=Number(url.searchParams.get('azimuth')),pitch=Number(url.searchParams.get('pitch'));
   state.photos.push({id:'photo'+uploads,ring:guide.ringOf(pitch,plan),sector:guide.sectorOf(azimuth,plan),azimuth,pitch});
   body={added:true,scan:snapshot()};
  }else if(url.pathname.endsWith('/finish')){finishes++;state.job={state:'queued',message:'VGGT queued'};body=snapshot();}
  else body=snapshot();
  return{ok:true,json:async()=>structuredClone(body)};
 }
 const sandbox={guide,console,URLSearchParams,Uint8Array,fetch,performance:{now:()=>now},
  location:{search:'?scan='+state.id,href:''},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,
  localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{getElementById:el,createElement:()=>new Element(),hidden:false,addEventListener:(name,fn)=>docEvents[name]=fn},
  window:{addEventListener:(name,fn)=>events[name]=fn,
   DeviceOrientationEvent:{requestPermission(){permissionCalls.push('orientation');return Promise.resolve('granted');}},
   DeviceMotionEvent:{requestPermission(){permissionCalls.push('motion');return Promise.resolve('granted');}}},
  navigator:{mediaDevices:{async getUserMedia(){if(denied){const e=Error('Denied');e.name='NotAllowedError';throw e;}return{getTracks:()=>[{stop(){stops++;}}]};}}}
 };
 vm.createContext(sandbox);await vm.runInContext(source,sandbox);await flush();
 await nodes.start.onclick();assert.deepEqual(permissionCalls,['orientation','motion']);
 assert.equal(creations,0,'A laptop pairing link captures into that exact scan.');
 assert.equal(state.options.turntable,false,'Phone keeps the full-image stationary-object VGGT path.');
 assert.equal(uploads,0,'Starting camera saves no implicit photo.');
 now=1000;vm.runInContext('tick()',sandbox);assert.equal(uploads,0,'No sensors means manual shutter.');
 function sensors(alpha=0,acceleration=0){events.deviceorientation({alpha,beta:80,gamma:0});events.devicemotion({rotationRate:{alpha:0,beta:0,gamma:0},acceleration:{x:acceleration,y:0,z:0}});}
 sensors();vm.runInContext('tick()',sandbox);now+=200;sensors();vm.runInContext('tick()',sandbox);assert.equal(uploads,0);
 now+=250;sensors();vm.runInContext('tick()',sandbox);await flush();assert.equal(uploads,1,'Auto waits for a sustained steady angle.');
 now+=1200;sensors(30,2);vm.runInContext('tick()',sandbox);now+=500;sensors(30,2);vm.runInContext('tick()',sandbox);await flush();
 assert.equal(uploads,1,'Acceleration blocks automatic capture even without rotation.');
 now+=2000;vm.runInContext('tick()',sandbox);await flush();assert.equal(uploads,1,'Stale sensor samples cannot trigger capture.');
 holdUpload=true;nodes.shutter.onclick();await flush();assert(nodes.shutter.disabled);nodes.shutter.onclick();await flush();assert.equal(uploads,2,'Only one photo may be in flight.');
 await nodes.finish.onclick();assert.equal(finishes,0,'Finish cannot overtake a pending upload.');
 const release=holdUpload;holdUpload=null;release();await flush();
 await nodes.lastShot.onclick();assert.equal(state.photos.length,1,'Undo updates saved photos and coverage.');
 await nodes.shutter.onclick();await nodes.shutter.onclick();assert.equal(state.photos.length,3);
 nodes.pause.onclick();assert(nodes.hud.hidden);assert.equal(nodes.realign.hidden,false);const released=stops;
 await nodes.realignNow.onclick();assert.equal(nodes.hud.hidden,false);assert.equal(state.photos.length,3,'Resume keeps saved coverage.');
 await nodes.finish.onclick();assert.equal(finishes,1);assert(stops>0,'Finish releases camera tracks.');assert.equal(nodes.done.hidden,false);
 assert(stops>released,'The resumed camera is released on Finish.');
 // A new tap that fails camera permission leaves the upload fallback usable.
 denied=true;await nodes.start.onclick();assert(nodes.introError.textContent.includes('blocked'));assert.equal(nodes.files.disabled,false);
 console.log('Phone UI passed: permissions, sensor fallback, steady auto capture, bounded uploads, undo, Finish, camera cleanup, permission failure.');
})().catch(e=>{console.error(e);process.exit(1);});
