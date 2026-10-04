import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compiled=ts.transpileModule(readFileSync(new URL('../src/lib/page.ts',import.meta.url),'utf8'),
 {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
function load(extra){const ctx={exports:{},...extra};vm.runInNewContext(compiled,ctx);return ctx.exports;}
function field({label='Amount',value='120',type='text',visible=true,attrs={},text=''}={}) {
 return {type,value,innerText:text,id:'',name:'',labels:label?[{innerText:label}]:[],offsetParent:null,
  getClientRects:()=>visible?[{}]:[],getAttribute:key=>attrs[key]??null,closest:()=>null};
}
function root(nodes,text='') {return {children:[{innerText:text,getClientRects:()=>[{}]}],querySelectorAll:selector=>selector==='*'?nodes:selector==='iframe, frame'?[]:nodes.filter(n=>!n.shadowRoot),querySelector:()=>null};}

test('snapshot reads fixed controls, custom ARIA fields and open shadows, excludes secrets/hidden fields',()=>{
 const custom=field({label:'Status',value:undefined,attrs:{role:'combobox','aria-valuetext':'on_hold'}});
 const shadow=root([field({label:'Receipt',value:'missing'})],'Receipt details');
 const document={...root([field(),custom,field({type:'password',value:'secret'}),field({visible:false,value:'hidden'}),{shadowRoot:shadow}]),
  body:{innerText:'Synthetic expense record'},title:'Synthetic fixture'};
 // Explicitly omit a native value property on a custom control.
 delete custom.value;
 const page=load({document,location:{pathname:'/expense',search:''},CSS:{escape:s=>s},getComputedStyle:()=>({visibility:'visible',display:'block'}),HTMLSelectElement:class{}}).snapshotFrame();
 assert.deepEqual(Array.from(page.fields),['- Amount: 120','- Status: on_hold','- Receipt: missing']);
 assert.ok(page.text.includes('Receipt details')); assert.equal(JSON.stringify(page).includes('secret'),false);
});

test('switching tabs prevents a wrong screenshot but still returns saved text context',async()=>{
 let screenshots=0;
 const result={url:'/fixture',title:'Synthetic',fields:['- Amount: 120'],text:'Synthetic expense',truncated:false,unreadableEmbeds:1};
 const chrome={tabs:{get:async()=>({id:11,title:'Interview'}),query:async()=>[{id:22,url:'https://other.test',windowId:1}],
  captureVisibleTab:async()=>{screenshots++;return 'data:image/jpeg;base64,wrong';}},scripting:{executeScript:async()=>[{frameId:0,result}]}};
 const snapshot=await load({chrome}).readCaptureSnapshot(11,true);
 assert.ok(snapshot.page.includes('Amount: 120')); assert.equal(snapshot.image_base64,undefined);
 assert.equal(screenshots,0); assert.ok(snapshot.warnings.some(w=>w.includes('Screenshot unavailable')));
 assert.ok(snapshot.warnings.some(w=>w.includes('embedded content')));
});

test('empty and truncated pages have distinct, visible quality warnings',async()=>{
 for(const fixture of [{fields:[],text:'',truncated:false},{fields:['- Amount: 120'],text:'Expense',truncated:true}]){
  const chrome={tabs:{get:async()=>({id:11,title:'Fixture'})},scripting:{executeScript:async()=>[{frameId:0,result:{url:'/',title:'',unreadableEmbeds:0,...fixture}}]}};
  const page=await load({chrome}).readPageSnapshot(11);
  assert.ok(page.warnings.some(w=>w.includes(fixture.truncated?'shortened':'No readable')));
 }
});

test('a tab switch during screenshot capture discards the image and retains text',async()=>{
 let queries=0;
 const chrome={tabs:{get:async()=>({id:11,title:'Fixture'}),query:async()=>[{id:++queries===1?11:22,url:'https://fixture.test',windowId:1}],
  captureVisibleTab:async()=> 'data:image/jpeg;base64,wrong-tab'},scripting:{executeScript:async()=>[{frameId:0,
   result:{url:'/',title:'Fixture',fields:['- Amount: 120'],text:'Expense',truncated:false,unreadableEmbeds:0}}]}};
 const snapshot=await load({chrome}).readCaptureSnapshot(11,true);
 assert.equal(snapshot.image_base64,undefined);assert.ok(snapshot.page.includes('Amount: 120'));
 assert.ok(snapshot.warnings.some(w=>w.includes('Screenshot unavailable')));
});

test('screenshot preview remains usable when DOM injection is denied and needs no event logger',async()=>{
 let messages=0;
 const chrome={tabs:{query:async()=>[{id:11,url:'https://fixture.test',windowId:1}],get:async()=>({id:11}),
  captureVisibleTab:async()=> 'data:image/jpeg;base64,picture',sendMessage:async()=>{messages++;throw new Error('Missing logger');}},
  scripting:{executeScript:async()=>{throw new Error('DOM denied');}}};
 const preview=await load({chrome}).readScreenPreview();
 assert.equal(preview.image_base64,'picture');assert.equal(preview.page,'');assert.equal(messages,0);
});

test('a live interview cannot share a screenshot from another tab',async()=>{
 let screenshots=0;
 const chrome={tabs:{query:async()=>[{id:22,url:'https://other.test',windowId:1}],
  captureVisibleTab:async()=>{screenshots++;return 'data:image/jpeg;base64,wrong';}}};
 await assert.rejects(load({chrome}).readScreenPreview(11),/Switch back to the interview tab/);
 assert.equal(screenshots,0);
});
