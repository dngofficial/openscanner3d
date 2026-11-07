// Run with Node.js. DOM/socket fixtures exercise live capture and Finish handoff.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(__dirname+'/index.html','utf8').split('<script>')[1].split('</script>')[0];
let drawings=0,builds=0,latestSocket;
const ctx=new Proxy({},{get:(o,k)=>o[k]||(()=>drawings++)});
class Element{
 constructor(){this.style={};this.children=[];this.checked=true;this.width=640;this.height=480;this.videoWidth=640;this.videoHeight=480;this.readyState=2;this.options=[];this.value='';}
 getContext(){return ctx;} addEventListener(){} setPointerCapture(){} setAttribute(k,v){this[k]=v;}
 removeAttribute(k){delete this[k];} replaceChildren(...v){this.children=[...v];}
 append(...v){this.children.push(...v);} add(v){this.options.push(v);} play(){return Promise.resolve();}
 getBoundingClientRect(){return {left:0,top:0,width:640,height:480};}
 toBlob(fn){fn({frame:true});}
}
const nodes={},el=id=>nodes[id]??=new Element();
const state={id:'a'.repeat(32),state:'collecting',stage:'capture',photos:[],progress:0,message:'Ready',outputs:[]};
const clone=o=>JSON.parse(JSON.stringify(o));
async function fetch(path,options={}){
 let data=state;
 if(path==='/api/health')data={colmap:true};
 else if(path==='/api/depth')data={state:'ready',device:'cpu',installed:true,error:''};
 else if(path.endsWith('/build')){builds++;state.state='queued';state.message='Aligning';}
 else if(path.endsWith('/merged_preview.json'))data={points:[[0,0,0,100,120,130],[1,1,1,140,150,160]],total:2};
 return {ok:true,json:async()=>clone(data)};
}
class Socket{
 constructor(){this.readyState=1;latestSocket=this;queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type:'ready'})}));}
 send(data){if(typeof data==='string'){let type=data==='capture'?'capture':'settings';queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type})}));}else this.pending=true;}
 deliver(){
  assert(this.pending);this.pending=false;
  state.photos.push({id:'frame'+state.photos.length});state.processed_frames=state.photos.length;
  this.onmessage({data:JSON.stringify({type:'frame',cloud:[[0,0,1,100,120,130],[1,1,2,140,150,160]],
   local_points:2,depth:'test',mask:'test',processed:state.processed_frames,saved_count:state.photos.length,
   saved:true,features:100,seconds:.1,foreground:.5,note:'Saved',limit_reached:false})});
 }
 close(){this.readyState=3;this.onclose?.();}
}
const store=new Map();
const sandbox={document:{getElementById:el,createElement:()=>new Element()},WebSocket:Socket,fetch,console,
 setTimeout:()=>1,clearTimeout(){},window:{addEventListener(){}},location:{protocol:'http:',host:'localhost:8000'},
 localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},
 navigator:{mediaDevices:{enumerateDevices:async()=>[],getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 vm.createContext(sandbox);await vm.runInContext(script,sandbox);await flush();
 assert.equal(nodes.start.disabled,false);
 await nodes.start.onclick();
 for(let i=0;i<3;i++){vm.runInContext('sendFrame()',sandbox);latestSocket.deliver();await flush();}
 assert.equal(nodes.saved.textContent,'3 / 200');
 assert.equal(nodes.finish.disabled,false);
 vm.runInContext('sendFrame()',sandbox);
 await nodes.finish.onclick();
 assert.equal(builds,0,'Finish must wait for in-flight depth processing');
 latestSocket.deliver();await flush();await flush();
 assert.equal(builds,1);
 assert.equal(nodes.start.disabled,true);
 state.state='completed';state.progress=100;state.message='Merged';state.accepted_clouds=4;state.merged_points=2;
 state.outputs=['merged.ply','merged_preview.json','alignment.json'];
 await vm.runInContext('refreshScan(false)',sandbox);await flush();
 assert.equal(nodes.downloads.children.length,2);
 assert.equal(nodes.aligned.textContent,4);
 assert(drawings>10);
 assert.equal(nodes.error.textContent,'');
 console.log('Live UI checks passed: camera capture, per-frame clouds, deferred Finish, build locking and final merged downloads.');
})().catch(e=>{console.error(e);process.exit(1)});
