// State/control integration checks with DOM/API/socket fixtures, no model weights.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=fs.readFileSync(__dirname+'/index.html','utf8').split('<script>')[1].split('</script>')[0];
let draws=0,finishes=0,latestSocket;
const context=new Proxy({},{get:(o,k)=>o[k]||(()=>draws++)});
class Element{
 constructor(){this.children=[];this.style={};this.checked=true;this.width=640;this.height=480;this.videoWidth=640;this.videoHeight=480;this.readyState=2;this.options=[];this.value='';this.files=[];}
 getContext(){return context;}append(...v){this.children.push(...v);}replaceChildren(...v){this.children=[...v];}
 setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}addEventListener(){}setPointerCapture(){}
 getBoundingClientRect(){return{left:0,top:0,width:640,height:480};}play(){return Promise.resolve();}
 add(v){this.options.push(v);}click(){}toBlob(fn){fn({frame:true});}
}
const nodes={},el=id=>nodes[id]??=new Element();
const state={id:'a'.repeat(32),photos:[],revision:0,processed:0,auto:true,finish_requested:false,
 options:{max_views:12,image_size:336,confidence:35,consistency:true,cleanup:true,foreground:true,interval:8},
 capture_settings:{roi:[.1,.08,.9,.92],background:true,denoise:true,preview:false},
 job:{state:'idle',stage:'capture',progress:0,message:'Waiting'},latest_result:null};
const clone=o=>JSON.parse(JSON.stringify(o));
async function fetch(path,options={}){
 let data=state;
 if(path==='/api/health')data={vggt:{installed:true,state:'ready',device:'cpu'},preview:{state:'unloaded'}};
 else if(path.endsWith('/preview.json'))data={points:[[0,0,1,100,120,130],[1,1,2,140,150,160]],total:2};
 else if(path.endsWith('/finish')){finishes++;state.finish_requested=true;state.job={id:'b'.repeat(32),state:'queued',progress:0,message:'Queued',final:true};}
 else if(path.endsWith('/photos')||path.includes('/photos?')){state.photos.push({id:'upload',name:'Uploaded angle'});state.revision++;data={added:true,scan:state};}
 else if(path.endsWith('/resume'))state.finish_requested=false;
 else if(path.endsWith('/settings')){const values=JSON.parse(options.body);state.options=values;state.auto=values.auto;}
 return{ok:true,json:async()=>clone(data)};
}
class Socket{
 constructor(){this.readyState=1;latestSocket=this;queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type:'ready'})}));}
 send(data){if(typeof data==='string')queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type:data==='capture'?'capture':'settings'})}));else this.pending=true;}
 deliver(){assert(this.pending);this.pending=false;state.photos.push({id:'frame'+state.photos.length,name:'Angle',width:640,height:480});state.revision++;state.processed++;
 this.onmessage({data:JSON.stringify({type:'frame',processed:state.processed,saved_count:state.photos.length,features:100,sharpness:80,mask:'test',note:'Saved'})});}
 close(){this.readyState=3;this.onclose?.();}
}
const storage=new Map();
const sandbox={document:{getElementById:el,createElement:()=>new Element()},fetch,WebSocket:Socket,console,
 setTimeout:()=>1,clearTimeout(){},location:{protocol:'http:',host:'localhost:8000'},window:{addEventListener(){}},
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
 navigator:{mediaDevices:{enumerateDevices:async()=>[],getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 vm.createContext(sandbox);await vm.runInContext(script,sandbox);await flush();
 assert.equal(nodes.start.disabled,false);await nodes.start.onclick();
 for(let i=0;i<3;i++){vm.runInContext('sendFrame()',sandbox);latestSocket.deliver();await flush();}
 assert.equal(nodes.saved.textContent,'3 / 200');
 state.job={state:'running',progress:40,message:'VGGT working',final:false};
 await vm.runInContext('refreshScan(false)',sandbox);
 assert.equal(nodes.pause.disabled,false,'Capture remains active during periodic inference.');
 vm.runInContext('sendFrame()',sandbox);assert(latestSocket.pending);
 await nodes.finish.onclick();assert.equal(finishes,0,'Finish waits for the in-flight camera frame.');
 latestSocket.deliver();await flush();assert.equal(finishes,1);
 assert.equal(nodes.start.disabled,true,'Final build locks capture until resumed.');
 state.job={id:'b'.repeat(32),state:'completed',progress:100,message:'Done'};
 state.latest_result={id:'b'.repeat(32),frame_ids:['1','2','3','4'],captured_views:4,revision:state.revision,final:true,points:2};
 await vm.runInContext('refreshScan(false)',sandbox);await flush();
 assert.equal(nodes.downloads.children.length,4);
 assert(nodes.mapNote.textContent.includes('final pass'));assert(draws>10);
 assert.equal(nodes.error.textContent,'');
 console.log('UI checks passed: capture continues during periodic inference; Finish waits; final PLY downloads render.');
})().catch(error=>{console.error(error);process.exit(1)});
