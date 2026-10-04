import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

test('stalled requests abort safely, with enough time for live reasoning and a longer build deadline',async()=>{
 const source=readFileSync(new URL('../src/lib/api.ts',import.meta.url),'utf8').replace('import.meta.env.VITE_API_BASE','"https://fixture.test"');
 const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 for(const [path,expected] of [['/api/capture/sessions/s/observations',60000],['/api/capture/sessions/s/frames',60000],['/api/capture/sessions/s/apprentice/advance',60000],['/api/capture/sessions/s/workmap',200000]]) {
  let timer,delay,cleared=false;
  const ctx={exports:{},AbortController,chrome:{storage:{session:{get:async()=>({})}}},
   setTimeout:(fn,ms)=>{timer=fn;delay=ms;return 1;},clearTimeout:()=>cleared=true,
   fetch:async(_url,{signal})=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new Error('private transport details'))))};
  vm.runInNewContext(compiled,ctx);
  const request=ctx.exports.api(path,{body:{client_id:'stable-synthetic-id'}});
  await new Promise(resolve=>setImmediate(resolve)); assert.equal(delay,expected);timer();
  await assert.rejects(request,error=>error.message.includes('capture writes are retained')&&!error.message.includes('private'));
  assert.equal(cleared,true);
 }
});
