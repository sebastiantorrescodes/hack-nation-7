import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {webcrypto} from 'node:crypto';

const source=readFileSync(new URL('../src/lib/captureQueue.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const sandbox={exports:{}}; vm.runInNewContext(compiled,sandbox);
const {CaptureQueue,captureTabMatches}=sandbox.exports;
const storage=() => {
 const rows={};
 return {get:async key=>structuredClone({[key]:rows[key]}),set:async value=>Object.assign(rows,structuredClone(value))};
};

test('failed writes survive panel recreation and retain their original identities',async()=>{
 const disk=storage(); let sends=0;
 const first=new CaptureQueue('session',disk,async()=>{sends++;throw new Error('lost response');});
 const write={id:'turn-1',path:'/transcript',body:{client_id:'turn-1',text:'Synthetic expert explanation'}};
 await first.enqueue(write); await assert.rejects(first.flush()); assert.equal(await first.pending(),1);
 const recovered=new CaptureQueue('session',disk,async(path,body)=>{sends++;assert.equal(path,write.path);assert.deepEqual(body,write.body);});
 await recovered.flush(); assert.equal(await recovered.pending(),0); assert.equal(sends,2);
});

test('concurrent capture writes are serialized without losing a turn',async()=>{
 const disk=storage(),seen=[]; const q=new CaptureQueue('s',disk,async(_,body)=>seen.push(body.n));
 await Promise.all(Array.from({length:30},(_,n)=>q.enqueue({id:String(n),path:'/events',body:{n}})));
 await Promise.all([q.flush(),q.flush()]); assert.deepEqual(seen,Array.from({length:30},(_,n)=>n));
});

test('reusing an identity for different facts cannot overwrite queued evidence',async()=>{
 const q=new CaptureQueue('s',storage(),async()=>{});
 await q.enqueue({id:'one',path:'/events',body:{value:1}});
 await assert.rejects(q.enqueue({id:'one',path:'/events',body:{value:2}}));
 assert.equal(await q.pending(),1);
});

test('only the session tab is captured, and Stop rejects later actions',()=>{
 assert.equal(captureTabMatches(12,12,true),true);
 for(const args of [[12,13,true],[12,12,false],[null,12,true],[12,undefined,true]]) assert.equal(captureTabMatches(...args),false);
});

function contentHarness({tab=11,guardTab=11,response,cryptoApi={randomUUID:()=> 'synthetic-id'},timers={setTimeout,clearTimeout},production=false}={}) {
 const listeners={},overlays=[],messages=[],receivers=[],registrations={};
 class Input {}
 class Select {}
 const box=()=>({id:'',style:{},innerHTML:'',appendChild(){},remove(){}});
 const document={addEventListener:(name,fn)=>{listeners[name]=fn;registrations[name]=(registrations[name]??0)+1;},getElementById:()=>null,createElement:()=>box(),body:{appendChild:x=>overlays.push(x)}};
 const chrome={storage:{local:{get:async()=>({guardSave:{tabId:guardTab,sessionId:'session'}})},onChanged:{addListener(){}}},runtime:{onMessage:{addListener:fn=>receivers.push(fn)},sendMessage:async message=>{
  messages.push(message); if(message.type==='tab-identity')return {tabId:tab};
  if(message.type==='save-attempt') {if(response instanceof Error)throw response;return response;}
 }}};
 const code=production?readFileSync(new URL('../dist/content.js',import.meta.url),'utf8'):
  ts.transpileModule(readFileSync(new URL('../src/content.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const context=vm.createContext({document,chrome,HTMLInputElement:Input,HTMLSelectElement:Select,location:{pathname:'/fixture'},crypto:cryptoApi,...timers});
 vm.runInContext('const A="legacy-name"; let m=false;',context);
 const rerun=()=>vm.runInContext(code,context);rerun();
 return {listeners,overlays,messages,receivers,registrations,rerun};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function click(h){
 const result={prevented:false,replayed:0};
 const el={innerText:'Save',getAttribute:()=>'',click:()=>result.replayed++};
 h.listeners.click({target:{closest:()=>el},preventDefault:()=>result.prevented=true,stopImmediatePropagation(){}});
 return result;
}

test('missing tutor and network errors keep the protected save blocked',async()=>{
 for(const response of [undefined,new Error('offline'),{ok:false,message:'Missing field'}]){
  const h=contentHarness({response}); await settle(); const action=click(h); await settle();
  assert.equal(action.prevented,true); assert.equal(action.replayed,0); assert.ok(h.overlays.some(x=>x.innerHTML.includes('Save needs verification')));
 }
});

test('typing pauses commit without blur, and change/focusout do not duplicate that action',async()=>{
 const scheduled=new Map();let next=0;
 const h=contentHarness({timers:{setTimeout:fn=>{scheduled.set(++next,fn);return next;},clearTimeout:id=>scheduled.delete(id)}});
 const field={type:'text',value:'1',id:'amount',tagName:'INPUT',getAttribute:()=>null,
  closest:selector=>selector==='td'?null:field};
 h.listeners.focusin({target:field}); h.listeners.input({target:field}); field.value='120'; h.listeners.input({target:field});
 assert.equal(h.messages.filter(m=>m.type==='ui-action').length,0);
 for(const fn of Array.from(scheduled.values())) fn(); await settle();
 h.listeners.change({target:field}); h.listeners.focusout({target:field}); await settle();
 const actions=h.messages.filter(m=>m.type==='ui-action');
 assert.equal(actions.length,1); assert.equal(actions[0].event.new_value,'120'); assert.equal(actions[0].event.old_value,'1');
});

test('Stop flushes a pending custom-field edit; password values never enter messages',async()=>{
 const scheduled=new Map();let next=0;
 const h=contentHarness({timers:{setTimeout:fn=>{scheduled.set(++next,fn);return next;},clearTimeout:id=>scheduled.delete(id)}});
 const field={innerText:'on_hold',id:'status',tagName:'DIV',getAttribute:()=>null,
  closest:selector=>selector==='td'?null:field};
 // composedPath is required for editable controls inside an open shadow root.
 h.listeners.input({target:{},composedPath:()=>[field]});
 let acknowledged=false; h.receivers.forEach(fn=>fn({type:'capture-flush'},{},res=>acknowledged=res.ok));
 await settle(); assert.equal(acknowledged,true);
 const secret={...field,type:'password',value:'secret',closest:()=>secret};
 h.listeners.input({target:secret}); h.listeners.change({target:secret}); await settle();
 const actions=h.messages.filter(m=>m.type==='ui-action');
 assert.equal(actions.length,1); assert.equal(actions[0].event.new_value,'on_hold');
 assert.equal(JSON.stringify(h.messages).includes('secret'),false);
});

test('a verified save replays once; unrelated tabs are not intercepted',async()=>{
 const h=contentHarness({response:{ok:true}});await settle();const action=click(h);await settle();assert.equal(action.replayed,1);
 const other=contentHarness({tab:22,guardTab:11});await settle();const unrelated=click(other);await settle();assert.equal(unrelated.prevented,false);
 assert.equal(other.messages.some(m=>m.type==='save-attempt'),false);
});

test('keyboard form submission uses the same verification gate',async()=>{
 const h=contentHarness({response:{ok:false}});await settle();let prevented=false,replayed=0;
 h.listeners.submit({target:{requestSubmit:()=>replayed++},submitter:null,preventDefault:()=>prevented=true,stopImmediatePropagation(){}});
 await settle();assert.equal(prevented,true);assert.equal(replayed,0);assert.ok(h.messages.some(m=>m.type==='save-attempt'));
});

test('the content logger proves it is ready before an interview starts',()=>{
 const h=contentHarness(); let ready;
 h.receivers.forEach(fn=>fn({type:'capture-ready'},{},result=>ready=result));
 assert.equal(ready.version,'durable-events-v2');
});

test('built content script can be attached repeatedly without redeclaration or duplicate actions',async()=>{
 const h=contentHarness({production:true,guardTab:99});h.rerun();h.rerun();await settle();
 assert.equal(h.receivers.length,1);assert.ok(Object.values(h.registrations).every(n=>n===1));
 click(h);await settle();assert.equal(h.messages.filter(m=>m.type==='ui-action').length,1);
});

test('website actions still report when randomUUID is unavailable',async()=>{
 const h=contentHarness({guardTab:99,cryptoApi:{getRandomValues:b=>webcrypto.getRandomValues(b)}});
 await settle(); click(h); click(h); await settle();
 const actions=h.messages.filter(m=>m.type==='ui-action');
 assert.equal(actions.length,2);
 assert.match(actions[0].event.event_id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 assert.notEqual(actions[0].event.event_id,actions[1].event.event_id);
});

test('startup attaches missing or stale loggers, and healthy frames need no reinjection',async()=>{
 const page=readFileSync(new URL('../src/lib/page.ts',import.meta.url),'utf8');
 for(const initial of [undefined,{version:'old'},{version:'durable-events-v2'},new Error('Receiving end does not exist')]) {
  let response=initial,injections=0;
  const ctx={exports:{},chrome:{tabs:{sendMessage:async(id,message,options)=>{
   assert.equal(id,11);assert.equal(message.type,'capture-ready');assert.equal(options.frameId,0);
   if(response instanceof Error)throw response; return response;
  }},scripting:{executeScript:async args=>{
   assert.equal(args.target.tabId,11);
   if(args.files){assert.deepEqual(Array.from(args.files),['content.js']);injections++;response={version:'durable-events-v2'};}
   return [{frameId:0,result:true}];
  }}}};
  vm.runInNewContext(ts.transpileModule(page,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,ctx);
  const ready=await ctx.exports.ensureCaptureLogger(11);
  assert.equal(ready.frames,1);assert.equal(injections,initial?.version==='durable-events-v2'?0:1);
 }
});

test('Chrome permission failures get a distinct message and never loop injection',async()=>{
 let injections=0;
 const ctx={exports:{},chrome:{tabs:{sendMessage:async()=>{throw new Error('No listener');}},scripting:{executeScript:async args=>{
  if(args.files)injections++;throw new Error('Private Chrome permission details');
 }}}};
 const page=readFileSync(new URL('../src/lib/page.ts',import.meta.url),'utf8');
 vm.runInNewContext(ts.transpileModule(page,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,ctx);
 await assert.rejects(ctx.exports.ensureCaptureLogger(11),e=>/Chrome blocked access/.test(e.message)&&!e.message.includes('Private'));
 assert.equal(injections,1);
});

test('a blocked child frame leaves the connected main page usable and reports its limit',async()=>{
 const ctx={exports:{},chrome:{tabs:{sendMessage:async(_tab,_msg,{frameId})=>frameId===0?{version:'durable-events-v2'}:undefined},
  scripting:{executeScript:async args=>{if(args.files)throw new Error('Blocked iframe');return [{frameId:0},{frameId:3}];}}}};
 const page=readFileSync(new URL('../src/lib/page.ts',import.meta.url),'utf8');
 vm.runInNewContext(ts.transpileModule(page,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,ctx);
 const ready=await ctx.exports.ensureCaptureLogger(11);assert.equal(ready.frames,1);assert.equal(ready.warnings.length,1);
});
