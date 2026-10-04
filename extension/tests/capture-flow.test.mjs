import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {webcrypto} from 'node:crypto';

function compile(path) {return ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),
 {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;}
const queueCtx={exports:{}}; vm.runInNewContext(compile('../src/lib/captureQueue.ts'),queueCtx);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<8;i++)await tick();}
function deferred(){let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no;});return {promise,resolve,reject};}

function harness({previewError} = {}) {
 const hooks=[],effects=[],listeners=[],timers=new Map(),calls=[],contexts=[],models=[],disk={},voiceInputs=[];
 let cursor=0,nextTimer=0,voiceOptions,page='Synthetic expense: status pending',built;
 const session={id:'session',workflow_id:'wf',expert_name:'Expert',started_at:Date.now()/1000,phase:'capture',workmap_revision:0};
 const react={useRef:initial=>{const i=cursor++;return hooks[i]??(hooks[i]={current:initial});},
  useState:initial=>{const i=cursor++;if(!hooks[i])hooks[i]={value:initial};return [hooks[i].value,value=>{hooks[i].value=typeof value==='function'?value(hooks[i].value):value;}];},
  useEffect:fn=>{const i=cursor++;if(!hooks[i]){hooks[i]={};effects.push(fn);}}};
 const storage={get:async key=>structuredClone({[key]:disk[key]}),set:async value=>Object.assign(disk,structuredClone(value)),remove:async keys=>{for(const key of keys)delete disk[key];}};
 const conversation={status:'disconnected',isSpeaking:false,sendContextualUpdate:text=>contexts.push(text),
  startSession:async()=>{conversation.status='connected';},endSession:async()=>{conversation.status='disconnected';voiceOptions.onDisconnect();}};
 const api=async(path,init)=>{
  calls.push({path,body:structuredClone(init?.body)});
  if(path==='/api/capture/sessions')return session;
  if(path==='/api/voice/signed-url')return {signed_url:'synthetic'};
  if(path==='/api/capture/screen-preview'){
   if(previewError)throw new Error(previewError);
   return {summary:'Synthetic expense screen',fields:{status:'pending'},uncertainties:[]};
  }
  if(path.endsWith('/frames')){const m=deferred();models.push(m);return m.promise;}
  if(path.endsWith('/workmap'))return {id:'workmap',revision:1,skills:[{title:'Hold missing receipt'}]};
  if(path.endsWith('/debrief')){session.phase='debrief';return {};}
  if(path.endsWith('/session'))return session;
  return {};
 };
 const dependencies={'react':react,'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},
  '@elevenlabs/react':{useConversation:options=>{voiceOptions=options;return conversation;}},'../lib/api':{api},
  '../lib/captureQueue':queueCtx.exports,'../lib/voice':{prepareVoiceAudio:async()=>({}),captureVoiceContext:(name,page,bounded,workflow)=>{voiceInputs.push({name,page,bounded,workflow});return page;}},
  '../lib/page':{getAppTab:async()=>({id:11,url:'https://fixture.test'}),ensureCaptureLogger:async()=>({frames:1,warnings:[]}),flushCaptureEdits:async()=>{},snapshotPage:async()=>page,
   readScreenPreview:async()=>({page,image_base64:'synthetic-image'}),
   readCaptureSnapshot:async()=>({page,title:'Synthetic expense',capturedAt:Date.now(),frames:1,fields:3,warnings:[]})}};
 const ctx={exports:{},require:name=>{assert.ok(dependencies[name],name);return dependencies[name];},crypto:webcrypto,URL,
  navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}},
  window:{setTimeout:(fn,ms)=>{timers.set(++nextTimer,{fn,ms});return nextTimer;},clearTimeout:id=>timers.delete(id)},
  chrome:{storage:{local:storage},runtime:{onMessage:{addListener:fn=>listeners.push(fn),removeListener(){}}},
   tabs:{onUpdated:{addListener(){},removeListener(){}},get:async()=>({id:11,url:'https://fixture.test'}),update:async()=>{}}}};
 vm.runInNewContext(compile('../src/sidepanel/Capture.tsx'),ctx);
 const render=()=>{cursor=0;const tree=ctx.exports.default({workflowId:'wf',workflow:{id:'wf',name:'Synthetic expense review',objective:'Hold expenses missing a receipt'},expertName:'Expert',onRecordingChange(){},onBuilt:value=>built=value});while(effects.length)effects.shift()();return tree;};
 function find(node,text){if(Array.isArray(node))return node.map(n=>find(n,text)).find(Boolean);if(!node?.props)return;
  if(node.type==='button'&&node.props.children===text)return node;return find(node.props.children,text);}
 const button=text=>{const found=find(render(),text);assert.ok(found,`Missing button ${text}`);return found.props.onClick;};
 function findOption(node,text){if(Array.isArray(node))return node.map(n=>findOption(n,text)).find(Boolean);if(!node?.props)return;
  if(node.type==='label'&&Array.isArray(node.props.children)&&node.props.children.some(c=>typeof c==='string'&&c.includes(text)))return node.props.children.find(c=>c?.type==='input');
  return findOption(node.props.children,text);}
 const option=(text,value)=>{const found=findOption(render(),text);assert.ok(found,`Missing option ${text}`);found.props.onChange({target:{checked:value}});render();};
 render();
 return {calls,contexts,models,disk,voiceInputs,button,option,render,setPage:value=>page=value,get built(){return built;},
  emit:async(event,text)=>{
   const waits=listeners.map(fn=>new Promise(resolve=>{if(fn({type:'ui-action',event,text},{tab:{id:11}},resolve)!==true)resolve();}));
   await Promise.all(waits);
  },timer:ms=>{const entry=Array.from(timers).find(([,v])=>v.ms===ms);assert.ok(entry,`Missing ${ms}ms timer`);timers.delete(entry[0]);entry[1].fn();}};
}

test('new page context reaches voice while older reasoning is pending; stale questions are suppressed',async()=>{
 const h=harness();h.option('Add automatic screen analysis',true);await h.button('Start interview')();await settle();assert.equal(h.models.length,1);
 h.setPage('Synthetic expense: status on_hold');
 await h.emit({event_id:'field-1',event_type:'field_change',target:'Status',new_value:'on_hold'},'changed status');
 h.timer(350);await settle();
 assert.ok(h.contexts.some(c=>c.includes('Synthetic expense: status on_hold')));
 assert.equal(h.models.length,1,'Fresh context must not wait for a second model request');
 assert.ok(h.calls.some(c=>c.path.endsWith('/observations')&&c.body.page?.includes('on_hold')));
 const old=h.calls.find(c=>c.path.endsWith('/frames')).body;
 h.models[0].resolve({t:old.t,description:'Older screen',is_decision_point:true,ask_why:'Question about the older screen?'});await settle();
 assert.equal(h.contexts.some(c=>c.includes('Question about the older screen?')),false);
});

test('ordinary interview shares workflow, page and actions without extra screen-model requests',async()=>{
 const h=harness();await h.button('Start interview')();await settle();
 assert.equal(h.models.length,0);
 assert.ok(h.contexts.some(c=>c.includes('Synthetic expense: status pending')));
 assert.ok(h.voiceInputs.some(c=>JSON.parse(c.workflow).objective==='Hold expenses missing a receipt'));
 const before=h.contexts.length;
 await h.emit({event_id:'click-1',event_type:'click',target:'Save'},'clicked Save');
 h.timer(350);await settle();
 assert.equal(h.models.length,0);
 assert.ok(h.contexts.slice(before).some(c=>c.includes('clicked Save')),'An action must reach voice even when the page text is unchanged');
 assert.ok(h.calls.some(c=>c.path.endsWith('/observations')&&c.body.events?.some(e=>e.event_id==='click-1')));
 await h.button('Stop')();await h.button('Build Work Map')();
 assert.equal(h.built.skills.length,1);
});

test('Check screen sends one image without microphone, action logger or capture session',async()=>{
 const h=harness();await h.button('Check screen')();
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/api/capture/screen-preview');
 assert.equal(h.calls[0].body.image_base64,'synthetic-image');assert.equal(h.models.length,0);
 assert.deepEqual(h.contexts,[]);
});

test('free-model failure retains the local screenshot and leaves the interview available',async()=>{
 const h=harness({previewError:'Synthetic free quota exhausted'});await h.button('Check screen')();
 const tree=JSON.stringify(h.render());
 assert.ok(tree.includes('data:image/jpeg;base64,synthetic-image'));
 assert.ok(tree.includes('Synthetic free quota exhausted'));
 assert.equal(h.calls.length,1,'A failure must not trigger an automatic model retry');
 await h.button('Start interview')();await settle();
 assert.equal(h.models.length,0);
 assert.ok(h.calls.some(c=>c.path==='/api/capture/sessions'));
});

test('Stop and Build Work Map use saved facts without waiting for a hung screen model',async()=>{
 const h=harness();h.option('Add automatic screen analysis',true);await h.button('Start interview')();await settle();
 await h.emit({event_id:'field-1',event_type:'field_change',target:'Status',new_value:'on_hold'},'changed status');
 const stopped=h.button('Stop')();
 await Promise.race([stopped,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Stop waited for screen model')),500))]);
 assert.ok(h.calls.some(c=>c.path.endsWith('/debrief')));
 await h.button('Build Work Map')();assert.equal(h.built.skills.length,1);
 h.models[0].reject(new Error('Synthetic provider unavailable'));await settle();
 assert.equal(h.disk['capture-analysis:session'],undefined,'A late failure must not restore a completed interview cache');
 assert.ok(h.calls.some(c=>c.path.endsWith('/observations')&&c.body.events?.some(e=>e.event_id==='field-1')));
});
