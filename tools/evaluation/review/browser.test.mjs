import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const {chromium}=require('@playwright/test');
const base=process.env.REVIEW_URL||'http://127.0.0.1:8766';
test('review import, explicit acceptance, invalidation, export and restore',async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  const d={dataset_id:'CSC442',samples:[{sample_id:'q1',question:'What is a stack? <img src=x onerror=alert(1)>',reference_answer:'A LIFO structure.',reference_contexts:['Stacks are last-in first-out.'],review_status:'approved'}]};
  await page.locator('#files').setInputFiles({name:'course.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(d))});
  await page.locator('#rows button').waitFor();assert.equal(await page.locator('#remaining').textContent(),'1');assert.equal(await page.locator('#rows img').count(),0);
  await page.locator('#reviewer').fill('Test Reviewer');await page.locator('#rows button').click();
  await page.locator('#edit-decision').selectOption('Accept');await page.getByRole('button',{name:'Save review',exact:true}).click();
  await page.locator('#form-error').filter({hasText:'confirmation'}).waitFor();
  await page.locator('#attest').check();await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#editor').waitFor({state:'hidden'});
  assert.equal(await page.locator('#accepted').textContent(),'1');await page.reload();await page.locator('#rows button').waitFor();assert.equal(await page.locator('#accepted').textContent(),'1');
  await page.locator('#rows button').click();await page.locator('#edit-reference').fill('Corrected reference');assert.equal(await page.locator('#edit-decision').inputValue(),'Pending');
  await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#editor').waitFor({state:'hidden'});assert.equal(await page.locator('#accepted').textContent(),'0');
  const downloadPromise=page.waitForEvent('download');await page.locator('#export-json').click();const download=await downloadPromise;const path=await download.path();
  page.once('dialog',d=>d.accept());await page.locator('#clear').click();assert.equal(await page.locator('#total').textContent(),'0');
  await page.locator('#files').setInputFiles(path);await page.locator('#rows button').waitFor();assert.equal(await page.locator('#remaining').textContent(),'1');
  await page.locator('#rows button').click();assert.equal(await page.locator('#edit-reference').inputValue(),'Corrected reference');assert.equal(await page.locator('#history .history-entry').count(),2);await page.locator('#close').click();
  await page.screenshot({path:process.env.REVIEW_SCREENSHOT||'tmp/review-workspace.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
test('real 100-question dataset, multiple courses, pagination and decision filters',async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();await page.goto(base);
  await page.locator('#files').setInputFiles(fileURLToPath(new URL('../../../evaluation/ragas/datasets/eduground_100q.gemini-v1.draft.json',import.meta.url)));
  await page.waitForFunction(()=>document.getElementById('total').textContent==='100');assert.equal(await page.locator('#rows tr').count(),25);
  await page.locator('#next').click();assert.match(await page.locator('#page-info').textContent(),/26–50/);
  await page.locator('#demo').click();await page.waitForFunction(()=>document.getElementById('total').textContent==='101');
  await page.locator('#course').selectOption('EXAMPLE');assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('#rows button').click();await page.locator('#edit-reviewer').fill('Human Reviewer');await page.locator('#edit-decision').selectOption('Flag');
  await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#form-error').filter({hasText:'Explain'}).waitFor();
  await page.locator('#edit-notes').fill('Check original evidence.');await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.locator('#decision-filter').selectOption('Flag');assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('#search').fill('unmatched question');assert.equal(await page.locator('#rows tr').count(),0);
 }finally{await browser.close();}
});
test('v2 review keeps multiline evidence bound to chunks and accepts unanswerable items',async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();await page.goto(base);
  const d={schema_version:2,dataset_id:'draft',course_id:'physics',notebook_id:'n1',source_manifest:[
   {source_id:'s1',sha256:'a'.repeat(64),version_number:1},{source_id:'s2',sha256:'b'.repeat(64),version_number:1}],samples:[
   {sample_id:'q1',question:'What follows from the passages?',reference_answer:'A result.',answerable:true,evidence:[
    {source_id:'s1',chunk_id:'c1',quote:'First\n\nparagraph.'},{source_id:'s2',chunk_id:'c2',quote:'Second.'}]},
   {sample_id:'q2',question:'What is the secret code?',reference_answer:null,answerable:false,evidence:[]}
  ]};
  await page.locator('#files').setInputFiles({name:'physics.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(d))});
  const evidence={schema_version:1,course_id:'physics',notebook_id:'n1',sources:[
   {source_id:'s1',checksum_sha256:'a'.repeat(64),version_number:1,chunks:[{chunk_id:'c1',text:'Before. First\n\nparagraph. After.',start_page:2}]},
   {source_id:'s2',checksum_sha256:'b'.repeat(64),version_number:1,chunks:[{chunk_id:'c2',text:'Second. After.',start_page:3}]}]};
  await page.locator('#files').setInputFiles({name:'evidence.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(evidence))});
  await page.locator('#rows button').first().waitFor();await page.locator('#reviewer').fill('Human');
  await page.getByRole('button',{name:'q1'}).click();
  assert.equal(await page.locator('.evidence-quote').count(),2);
  assert.match(await page.locator('#source-passages').textContent(),/Before\. First/);
  await page.locator('.evidence-quote').first().fill('First\n\nparagraph.');
  await page.locator('#edit-decision').selectOption('Accept');await page.locator('#attest').check();
  await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'q2'}).click();
  assert.equal(await page.locator('.evidence-quote').count(),0);
  await page.locator('#edit-decision').selectOption('Accept');await page.locator('#attest').check();
  await page.getByRole('button',{name:'Save review',exact:true}).click();await page.locator('#editor').waitFor({state:'hidden'});
  assert.equal(await page.locator('#accepted').textContent(),'2');
  const downloadPromise=page.waitForEvent('download');await page.locator('#export-json').click();
  const downloaded=await downloadPromise;const {readFileSync}=await import('node:fs');
  const bundle=JSON.parse(readFileSync(await downloaded.path(),'utf8'));
  assert.equal(bundle.packets[0].reviews[0].supporting_evidence[0].chunk_id,'c1');
  assert.equal(bundle.packets[0].reviews[0].supporting_evidence[0].quote,'First\n\nparagraph.');
  assert.deepEqual(bundle.packets[0].reviews[1].supporting_evidence,[]);
 }finally{await browser.close();}
});
