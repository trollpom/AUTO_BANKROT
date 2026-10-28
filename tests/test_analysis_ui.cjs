/* Optional DOM smoke test: npm install --no-save --package-lock=false jsdom */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const dom = new JSDOM("<main></main>", { url:"http://127.0.0.1:8000", runScripts:"outside-only" });
const w = dom.window;
if (!w.crypto.randomUUID) w.crypto.randomUUID = require("node:crypto").randomUUID;
w.eval(fs.readFileSync(path.join(__dirname,"../app/static/analysis.js"),"utf8"));
const panel = w.LotAnalysis;
const tick = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for(let i=0;i<5;i++) await tick(); };
const lot={id:1,make:"Toyota",model:"Camry",year:2015,photos:[{id:8,status:"ok",local_path:"photos/test.jpg",source_site:"email_response"}]};
const report={mode:"offline",created_at:"2026-10-27",vehicle:{},market:{sample_size:3,mean_rub:1000000,median_rub:1000000,low_rub:950000,high_rub:1050000,
  warnings:[],comparables:[{url:"javascript:alert(1)",title:'<img src=x onerror="alert(1)">',price_rub:1000000}],excluded:[]},
  economics:{decision:"insufficient_data",winning_probability:{value:null},scenarios:[],notes:[],assumptions:{}}};

(async()=>{
  let calls=[];
  const root=()=>w.document.querySelector("#lotAnalysis");
  const fakeAPI=async(url,options)=>{
    calls.push({url,options});
    if(url==="/api/ai/status") return {configured:false,message:"Ключ не подключён",model:"deepseek-flash",max_photos:6};
    if(url.endsWith("/calculate")) return report;
    return {latest:null,last_success:null,history:[]};
  };
  w.document.querySelector("main").innerHTML=panel.shell(lot);
  panel.mount(root(),lot,fakeAPI); await settle();
  assert.equal(root().querySelector("[data-analyze]").disabled,true);
  const form=root().querySelector("form");
  root().querySelector("[data-add-comparable]").click();
  const row=root().querySelector("fieldset");
  row.querySelector('[data-c="url"]').value="https://auto.ru/cars/used/sale/toyota/camry/10000000-a/";
  row.querySelector('[data-c="price_rub"]').value="1000000";
  row.querySelector('[data-c="full_price"]').checked=true;
  form.elements.namedItem("costs.transport_rub").value="0";
  root().querySelector("[data-calculate]").click(); await settle();
  const sent=JSON.parse(calls.find(c=>c.url.endsWith("/calculate")).options.body);
  assert.equal(sent.costs.transport_rub,"0");
  assert.equal(sent.costs.storage_rub,null);
  assert.equal(sent.manual_comparables[0].full_price,true);
  assert.equal(sent.manual_comparables[0].make,"Toyota");
  assert.equal(sent.manual_comparables[0].year,2015);
  assert.equal("consent_to_external" in sent,false);
  assert.equal(root().querySelector("[data-report] img"),null);
  assert.equal(root().querySelector('[data-report] a[href^="javascript:"]'),null);
  assert.match(root().querySelector("[data-report]").textContent,/<img/);

  // Ready state: no provider call until consent and selected-photo identity are explicit.
  panel.unmount();
  w.document.querySelector("main").innerHTML=panel.shell(lot);
  calls=[];
  let done=false;
  const liveAPI=async(url,options)=>{
    calls.push({url,options});
    if(url==="/api/ai/status") return {configured:true,message:"Готов",model:"deepseek-flash",max_photos:6};
    if(options?.method==="POST") return {run:{id:"run1"},reuse:"new"};
    if(url.endsWith("/runs/run1")) {done=true;return {id:"run1",status:"done",stage:"done",report};}
    return {latest:done?{id:"run1",status:"done"}:null,last_success:done?{id:"run1",report}:null,history:[]};
  };
  panel.mount(root(),lot,liveAPI);await settle();
  const currentForm=root().querySelector("form");
  const submit=async()=>{currentForm.dispatchEvent(new w.Event("submit",{cancelable:true}));await settle();};
  await submit();assert.equal(calls.filter(c=>c.options?.method==="POST").length,0);
  currentForm.elements.namedItem("consent_to_external").checked=true;
  currentForm.elements.namedItem("photo_id").checked=true;
  await submit();assert.equal(calls.filter(c=>c.options?.method==="POST").length,0);
  currentForm.elements.namedItem("confirm_photo_identity").checked=true;
  await submit();
  const requests=calls.filter(c=>c.options?.method==="POST");assert.equal(requests.length,1);
  const payload=JSON.parse(requests[0].options.body);
  assert.deepEqual(payload.photo_ids,[8]);assert.equal(payload.consent_to_external,true);
  assert.ok(payload.request_id.length>=8);assert.equal(done,true);
  assert.match(root().querySelector("[data-run-status]").textContent,/завершён/);

  // Closing/changing a lot must discard late status responses.
  panel.unmount();w.document.querySelector("main").innerHTML=panel.shell(lot);
  let release;
  panel.mount(root(),lot,()=>new Promise(resolve=>{release=resolve;}));
  panel.unmount();
  release({configured:true,message:"LATE",model:"x",max_photos:6});await settle();
  assert.doesNotMatch(root().textContent,/LATE/);
  dom.window.close();
  console.log("UI smoke: offline inputs, escaping, consent, photo identity, run/poll and disposal OK");
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
