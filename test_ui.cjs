// Browser state checks using a small DOM fixture; no browser engine is required.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(__dirname+'/index.html','utf8');
const script=html.split('<script>')[1].split('</script>')[0];
let drawCalls=0;
const ctx=new Proxy({measureText:s=>({width:s.length*8})},{get:(o,k)=>k in o?o[k]:(()=>drawCalls++)});
class Element{
 constructor(){this.children=[];this.style={};this.checked=true;this.width=1000;this.height=440;this.classList={add(){},remove(){}};this.textContent='';this.value='mesh';}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=[...items];}
 setAttribute(key,value){this[key]=value;}
 addEventListener(){}
 getContext(){return ctx;}
 setPointerCapture(){}
}
const nodes={};
const element=id=>nodes[id]??=(new Element());
const state={id:'a'.repeat(32),photos:[],state:'collecting',stage:'photos',message:'Add photos',progress:0,registered_images:0,points:0,outputs:[]};
let counter=0;
const clone=x=>JSON.parse(JSON.stringify(x));
const fetch=async(path,options={})=>{
 let data;
 if(path==='/api/health')data={colmap:true};
 else if(path==='/api/scans'&&options.method==='POST')data=state;
 else if(path.endsWith('/preview.json'))data={points:[[0,0,0,255,0,0],[1,2,3,0,255,0]],total:2};
 else if(path.endsWith('/build')){state.state='queued';state.stage='queued';data=state;}
 else if(path.includes('/photos?')){
  const id=(++counter).toString().padStart(32,'0');
  const photo={id,name:decodeURIComponent(path.split('name=')[1]),warnings:[],features:100,width:640,height:480,sharpness:80,brightness:100,feature_points:[[.2,.3]],feature_box:[.1,.1,.9,.9]};
  state.photos.push(photo);data={duplicate:false,photo};
 }else if(options.method==='DELETE'){state.photos=state.photos.filter(p=>!path.endsWith(p.id));state.outputs=[];state.state='collecting';data=state;}
 else data=state;
 return {ok:true,json:async()=>clone(data)};
};
const store=new Map();
const sandbox={document:{getElementById:element,createElement:()=>new Element()},
 fetch,console,setTimeout:()=>1,clearTimeout(){},
 localStorage:{getItem:key=>store.get(key),setItem:(key,v)=>store.set(key,v)},
 Image:class{constructor(){this.naturalWidth=640;this.naturalHeight=480;}set src(v){queueMicrotask(()=>this.onload?.());}}};
(async()=>{
 vm.createContext(sandbox);
 await vm.runInContext(script,sandbox);
 assert.equal(nodes.build.disabled,true);
 sandbox.files=[{name:'front.jpg',size:10},{name:'side.jpg',size:10},{name:'back.jpg',size:10}];
 await vm.runInContext('uploadFiles(files)',sandbox);
 assert.equal(nodes.count.textContent,'3 / 200');
 assert.equal(nodes.gallery.children.length,3);
 assert.equal(nodes.build.disabled,false);
 assert(drawCalls>0);
 await nodes.build.onclick();
 assert.equal(nodes.build.disabled,true);
 assert.equal(nodes.cancel.style.display,'block');
 state.state='completed';state.stage='done';state.progress=100;state.registered_images=3;state.points=2;state.outputs=['sparse.ply','preview.json','results.zip'];
 await vm.runInContext('refresh()',sandbox);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(nodes.cancel.style.display,'none');
 assert.equal(nodes.downloads.children.length,2);
 assert(nodes.stats.textContent.includes('3 / 3'));
 await nodes.gallery.children[0].children[1].onclick();
 assert.equal(nodes.count.textContent,'2 / 200');
 assert.equal(nodes.build.disabled,true);
 console.log('UI checks passed: upload gallery, preview overlays, build locking, completed downloads and photo removal.');
})().catch(error=>{console.error(error);process.exit(1)});
