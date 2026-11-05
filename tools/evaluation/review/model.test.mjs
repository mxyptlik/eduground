import test from 'node:test';
import assert from 'node:assert/strict';
import {KIND,packetFromDataset,initialFields,makeReview,validateBundle,exportCSV,matchEvidencePacket,checkReviewEvidence} from './model.mjs';
const dataset=()=>({dataset_id:'test',samples:[{sample_id:'q1',question:'Question?',reference_answer:'Reference',reference_contexts:['Evidence'],review_status:'approved',reviewer:'automated gate'}]});
test('legacy automatic approval never becomes a human decision',async()=>{
 const p=await packetFromDataset(dataset(),'course.json');assert.equal(p.reviews.length,0);assert.equal(initialFields(p.dataset.samples[0]).decision,'Pending');
});
test('acceptance requires source attestation, flag requires a reason, revisions survive roundtrip',async()=>{
 const p=await packetFromDataset(dataset(),'course.json');const fields={...initialFields(p.dataset.samples[0]),reviewer:'Human',decision:'Accept'};
 await assert.rejects(makeReview(fields),/confirmation/);
 const r=await makeReview({...fields,evidence_checked:true});p.reviews.push(r);p.history.push(r);
 const b=JSON.parse(JSON.stringify({kind:KIND,packets:[p]}));await validateBundle(b);
 await assert.rejects(makeReview({...fields,decision:'Flag'}),/Explain/);
 const revised=await makeReview({...r,reference_answer:'Correction',decision:'Pending',evidence_checked:false},r);assert.equal(revised.revision,2);assert.notEqual(revised.content_hash,r.content_hash);
 b.packets[0].reviews[0].reference_answer='Tampered';await assert.rejects(validateBundle(b),/hash/);
});
test('duplicate IDs and altered original datasets are rejected',async()=>{
 const d=dataset();d.samples.push({...d.samples[0]});await assert.rejects(packetFromDataset(d,'bad.json'),/unique/);
 const p=await packetFromDataset(dataset(),'course.json');p.dataset.samples[0].question='Changed';await assert.rejects(validateBundle({kind:KIND,packets:[p]}),/hash/);
});
test('CSV escapes quotes, newlines and formula-like input',async()=>{
 const d=dataset();d.samples[0].question='=HYPERLINK("bad")\nsecond line';const p=await packetFromDataset(d,'course.json');
 const csv=exportCSV([p]);assert.ok(csv.includes('"\'=HYPERLINK(""bad"")\nsecond line"'));assert.ok(csv.includes('Pending'));
});
test('v2 multi-evidence drafts import as pending for human review',async()=>{
 const d={schema_version:2,dataset_id:'draft-2',course_id:'physics',samples:[
  {sample_id:'q2',question:'How do these facts connect?',reference_answer:'They connect.',answerable:true,
   evidence:[{source_id:'s1',chunk_id:'c1',quote:'First fact.'},{source_id:'s2',chunk_id:'c2',quote:'Second fact.'}]}
 ]};
 const p=await packetFromDataset(d,'physics.json');
 assert.equal(p.course_id,'physics');
 assert.equal(initialFields(d.samples[0]).supporting_passages,'First fact.\n\nSecond fact.');
 assert.equal(initialFields(d.samples[0]).decision,'Pending');
 await validateBundle({kind:KIND,packets:[p]});
});
test('v2 evidence edits retain source and chunk bindings, including blank lines',async()=>{
 const d={schema_version:2,dataset_id:'draft',course_id:'physics',samples:[{sample_id:'q2',question:'What follows from both passages?',reference_answer:'A result.',answerable:true,evidence:[{source_id:'s1',chunk_id:'c1',quote:'First\n\npart.'},{source_id:'s2',chunk_id:'c2',quote:'Second.'}]}]};
 const p=await packetFromDataset(d,'physics.json');
 const fields={...initialFields(d.samples[0]),decision:'Accept',reviewer:'Human',evidence_checked:true};
 const r=await makeReview(fields);p.reviews=[r];p.history=[r];
 await validateBundle({kind:KIND,packets:[p]});
 r.supporting_evidence[0].chunk_id='wrong';
 await assert.rejects(validateBundle({kind:KIND,packets:[p]}),/hash|source and chunk/);
});
test('v2 unanswerable question can be accepted without invented evidence',async()=>{
 const d={schema_version:2,dataset_id:'draft',course_id:'physics',samples:[{sample_id:'q3',question:'What is the hidden code?',reference_answer:null,answerable:false,evidence:[]}]};
 const p=await packetFromDataset(d,'physics.json');
 const r=await makeReview({...initialFields(d.samples[0]),answerable:false,reviewer:'Human',decision:'Accept',evidence_checked:true});
 assert.equal(r.supporting_passages,'');assert.deepEqual(r.supporting_evidence,[]);
 p.reviews=[r];p.history=[r];await validateBundle({kind:KIND,packets:[p]});
});
test('complete indexed evidence is source-bound and checks corrected quotes',async()=>{
 const d={schema_version:2,dataset_id:'draft',course_id:'physics',notebook_id:'n1',source_manifest:[{source_id:'s1',sha256:'a'.repeat(64),version_number:1}],samples:[
  {sample_id:'q1',question:'What does the passage say?',reference_answer:'A fact.',answerable:true,evidence:[{source_id:'s1',chunk_id:'c1',quote:'A fact.'}]}
 ]};
 const packet={schema_version:1,course_id:'physics',notebook_id:'n1',sources:[{source_id:'s1',checksum_sha256:'a'.repeat(64),version_number:1,chunks:[{chunk_id:'c1',text:'Before. A fact. After.'}]}]};
 const chunks=matchEvidencePacket(d,packet);
 const r=await makeReview({...initialFields(d.samples[0]),answerable:true,reviewer:'Human',decision:'Accept',evidence_checked:true});
 checkReviewEvidence(d.samples[0],r,chunks);
 r.supporting_evidence[0].quote='Invented.';
 assert.throws(()=>checkReviewEvidence(d.samples[0],r,chunks),/not in/);
 packet.sources[0].checksum_sha256='b'.repeat(64);
 assert.throws(()=>matchEvidencePacket(d,packet),/differs/);
});
