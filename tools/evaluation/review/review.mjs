import {KIND, packetFromDataset, initialFields, makeReview, validateBundle, exportCSV, matchEvidencePacket, checkReviewEvidence} from './model.mjs';
const $ = id => document.getElementById(id), KEY='eduground-review-v1';
let packets=[], page=0, active=null, dirty=false;const similarities=new Map();
const evidencePackets=new Map();
const PAGE_SIZE=25;
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
function bundle(){return {kind:KIND,exported_at_utc:new Date().toISOString(),packets};}
function persist(){try{localStorage.setItem(KEY,JSON.stringify(bundle()));message('Saved in this browser. Export the review bundle for a durable backup.');}catch{message('Browser storage is unavailable or full. Your work is in memory: export JSON before closing.',true);}}
function allRows(){return packets.flatMap(p=>p.dataset.samples.map(s=>({p,s,r:initialFields(s,p.reviews.find(r=>r.sample_id===s.sample_id))})));}
function filteredRows(){const q=$('search').value.toLowerCase();return allRows().filter(({p,s,r})=>(!$('course').value||p.course_id===$('course').value)&&(!$('decision-filter').value||r.decision===$('decision-filter').value)&&[s.sample_id,r.question,r.reference_answer].join(' ').toLowerCase().includes(q));}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function tidyPassage(text){
 const lines=String(text).replace(/\r/g,'').split('\n').map(l=>l.replace(/\s+$/,'').replace(/^\s*\u25a1\s*/,'\u2022 '));
 const out=[];let blanks=0;
 for(const l of lines){if(!l.trim()){blanks++;if(blanks>1)continue;}else blanks=0;out.push(l);}
 return out.join('\n').trim();
}
function passageNode(text,quote){
 const clean=tidyPassage(text);const div=el('div');div.className='passage';
 if(!quote){div.textContent=clean;return div;}
 const esc=quote.trim().replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\s+/g,'\\s+');
 let m=null;try{m=new RegExp(esc,'i').exec(clean);}catch{}
 if(!m){div.textContent=clean;return div;}
 div.append(document.createTextNode(clean.slice(0,m.index)));
 div.append(el('mark',m[0],'quote-mark'));
 div.append(document.createTextNode(clean.slice(m.index+m[0].length)));
 return div;
}
function refresh(){
 const course=$('course').value; $('course').replaceChildren(new Option('All courses',''));
 [...new Set(packets.map(p=>p.course_id))].sort().forEach(c=>$('course').add(new Option(c,c)));$('course').value=course;
 const rows=allRows();$('total').textContent=rows.length;
 for(const [id,state] of [['accepted','Accept'],['flagged','Flag'],['remaining','Pending'],['rejected','Reject']])$(id).textContent=rows.filter(x=>x.r.decision===state).length;
 const filtered=filteredRows();page=Math.max(0,Math.min(page,Math.ceil(filtered.length/PAGE_SIZE)-1));$('rows').replaceChildren();
 for(const entry of filtered.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE)){
  const {p,s,r}=entry,tr=el('tr'),first=el('td');first.append(el('small',p.course_id));const b=el('button',s.sample_id);b.type='button';b.addEventListener('click',()=>openEditor(entry));first.append(b);const sim=similarities.get(s.sample_id);if(sim&&sim.flagged){const badge=el('span',`⚠ sim ${sim.max_quote_similarity}`,'sim-badge');badge.title=`Quote similarity ${sim.max_quote_similarity} · chunk ${sim.max_chunk_similarity} — look closer`;first.append(badge);}tr.append(first);
  for(const value of [r.question,r.decision,r.reference_answer,r.supporting_passages,r.required_changes,r.reviewer,r.reviewed_at_utc?new Date(r.reviewed_at_utc).toISOString().replace('T',' ').slice(0,19):'—']){
   const td=el('td');td.append(el('span',value||'—',value===r.decision?'badge '+r.decision:'snippet'));tr.append(td);
  }$('rows').append(tr);
 }
 $('empty').hidden=rows.length>0;$('page-info').textContent=filtered.length?`${page*PAGE_SIZE+1}–${Math.min((page+1)*PAGE_SIZE,filtered.length)} of ${filtered.length} questions`:'No matching questions';
 $('previous').disabled=page===0;$('next').disabled=(page+1)*PAGE_SIZE>=filtered.length;$('export-json').disabled=!packets.length;$('export-csv').disabled=!packets.length;
}
function openEditor(entry){
 active=entry;dirty=false;const {p,s,r}=entry;$('item-id').textContent=`${p.course_id} / ${s.sample_id}`;$('edit-course').value=p.course_id;
 $('edit-question').value=r.question;$('edit-reference').value=r.reference_answer;$('edit-evidence').value=r.supporting_passages;$('edit-notes').value=r.required_changes;$('edit-decision').value=r.decision;$('edit-reviewer').value=$('reviewer').value||r.reviewer;
 const v2=p.dataset.schema_version===2;
 $('legacy-evidence-field').hidden=v2;$('edit-evidence').required=!v2;$('structured-evidence').hidden=!v2;
 $('structured-evidence').replaceChildren();
 if(v2){
   $('edit-reference').required=s.answerable;
   if(s.answerable){
     const saved=r.supporting_evidence||s.evidence;
     s.evidence.forEach((e,i)=>{
       const label=el('label',`Evidence ${i+1} · source ${e.source_id} · chunk ${e.chunk_id}`);
       const input=el('textarea');input.className='evidence-quote';input.rows=4;input.required=true;
       input.value=saved[i]?.quote||e.quote;label.append(input);$('structured-evidence').append(label);
     });
   }else $('structured-evidence').append(el('p','Unanswerable: no supporting passage is required. Check the question against the course scope.','muted'));
 }else $('edit-reference').required=true;
 $('attest').checked=false;$('form-error').textContent='';
 const ids=Array.isArray(s.source_ids)?s.source_ids:(s.evidence||[]).map(e=>e.source_id);const sources=(p.dataset.source_manifest||[]).filter(x=>ids.includes(x.source_id));
 $('source-info').textContent='';$('source-info').append(el('p',`Locator: ${s.expected_locator||'See evidence below'}\nChunk: ${s.source_chunk_id||'See evidence below'}\nType: ${s.question_type||'Unspecified'} · Difficulty: ${s.difficulty||'Unspecified'}`,'source-meta'));
 (s.evidence||[]).forEach((e,i)=>$('source-info').append(el('p',`Evidence ${i+1}: source ${e.source_id}, chunk ${e.chunk_id}, ${e.locator||'locator not supplied'}`,'source-meta')));
 sources.forEach(x=>$('source-info').append(el('p',`${x.source_label||x.source_id}\nSource: ${x.source_id}\nSHA256: ${x.sha256||'Not supplied'}`,'source-meta')));
 const sim=similarities.get(s.sample_id);if(sim){$('source-info').append(el('p',`Similarity alarm: quote ${sim.max_quote_similarity} ? chunk ${sim.max_chunk_similarity} ? ${sim.flagged?'FLAGGED - check whether this quote truly supports the question':'within tolerance'}`,'source-meta sim-note'+(sim.flagged?' sim-flagged':'')));}
 $('source-passages').replaceChildren();
 const indexed=evidencePackets.get(p.course_id);
 let completeChunks=null;
 if(indexed){try{completeChunks=matchEvidencePacket(p.dataset,indexed);}catch(err){$('source-info').append(el('p',`Evidence packet mismatch: ${err.message}`,'error'));}}
 (s.evidence||[]).forEach((item,i)=>{
   $('source-passages').append(el('p',`Draft quote ${i+1}: ${item.source_id} / ${item.chunk_id}`,'source-meta'));
   $('source-passages').append(passageNode(item.quote));
   const full=completeChunks?.get(item.chunk_id);
   if(full && full.source_id===item.source_id){
     $('source-passages').append(el('p',`Complete indexed chunk ${item.chunk_id} · page ${full.start_page??'—'} · slide ${full.start_slide??'—'}`,'source-meta'));
     $('source-passages').append(passageNode(full.text, item.quote));
   }
 });
 if(!s.evidence)(s.reference_contexts||[]).forEach(t=>$('source-passages').append(passageNode(t)));
 if(p.dataset.schema_version===2&&!indexed)$('source-info').append(el('p','Import the course evidence packet to inspect complete indexed chunks here.','muted'));
 $('original').textContent=`${s.question}\n\n${s.reference_answer}\n\nImported status: ${s.review_status||s.integrity||'Unspecified'} (not adopted as human approval)`;
 $('history').replaceChildren();const history=p.history.filter(x=>x.sample_id===s.sample_id);history.forEach(h=>{$('history').append(el('p',`Revision ${h.revision} · ${h.decision} · ${h.reviewer}\n${h.reviewed_at_utc}\n${h.required_changes||'No notes'}`,'history-entry'));});if(!history.length)$('history').textContent='No human decisions saved here yet.';
 $('editor').showModal();
}
function closeEditor(){if(dirty&&!confirm('Discard unsaved changes to this question?'))return;$('editor').close();active=null;dirty=false;}
$('close').onclick=closeEditor;$('editor').addEventListener('cancel',e=>{e.preventDefault();closeEditor();});
$('review-form').addEventListener('input',e=>{dirty=true;if(['edit-question','edit-reference','edit-evidence'].includes(e.target.id)||e.target.classList.contains('evidence-quote')){$('edit-decision').value='Pending';$('attest').checked=false;}});
$('review-form').addEventListener('submit',async e=>{
 e.preventDefault();const button=e.submitter;button.disabled=true;
 try{
  const course=$('edit-course').value.trim();if(!course)throw Error('Enter a course code.');
  const {p,s}=active,previous=p.reviews.find(x=>x.sample_id===s.sample_id);
  if(p.dataset.schema_version===2 && course!==p.dataset.course_id)throw Error('The course code must match the imported v2 dataset.');
  const v2=p.dataset.schema_version===2;
  const supporting_evidence=v2 ? s.evidence.map((item,i)=>({source_id:item.source_id,chunk_id:item.chunk_id,quote:$('structured-evidence').querySelectorAll('.evidence-quote')[i].value})) : undefined;
  const supporting_passages=v2 ? supporting_evidence.map(item=>item.quote).join('\n\n') : $('edit-evidence').value;
  const r=await makeReview({sample_id:s.sample_id,question:$('edit-question').value,reference_answer:$('edit-reference').value,supporting_passages,
    ...(v2?{supporting_evidence,answerable:s.answerable}:{}),required_changes:$('edit-notes').value,decision:$('edit-decision').value,reviewer:$('edit-reviewer').value.trim(),evidence_checked:$('attest').checked},previous);
  if(v2 && r.decision==='Accept' && evidencePackets.has(p.course_id))checkReviewEvidence(s,r,matchEvidencePacket(p.dataset,evidencePackets.get(p.course_id)));
  p.course_id=course;p.reviews=p.reviews.filter(x=>x.sample_id!==s.sample_id);p.reviews.push(r);p.history.push({...r});$('reviewer').value=r.reviewer;dirty=false;$('editor').close();active=null;persist();refresh();
 }catch(err){$('form-error').textContent=err.message;}finally{button.disabled=false;}
});
async function importData(data,name){
 if(data?.schema_version===1 && Array.isArray(data.sources) && typeof data.course_id==='string' && typeof data.notebook_id==='string'){
   const matches=packets.filter(p=>p.course_id===data.course_id && p.dataset.schema_version===2);
   for(const p of matches)matchEvidencePacket(p.dataset,data);
   evidencePackets.set(data.course_id,data);message(`Complete indexed evidence loaded for ${data.course_id} in this session. Re-import it after reopening the browser.`);return;
 }
 if(data?.items && Array.isArray(data.items) && typeof data.course_id==='string' && typeof data.kind==='string' && data.kind.startsWith('soft_similarity_alarm')){
  let flagged=0;for(const item of data.items){if(item&&typeof item.sample_id==='string'){similarities.set(item.sample_id,item);if(item.flagged)flagged++;}}
  message(`Similarity alarm loaded for ${data.course_id}: ${flagged} flagged of ${data.items.length} items. Session-only; a flag is a review cue, not a failure.`);refresh();return;
 }
 let incoming;if(data?.kind===KIND){await validateBundle(data);incoming=data.packets;}else incoming=[await packetFromDataset(data,name)];
 for(const p of incoming){if(evidencePackets.has(p.course_id))matchEvidencePacket(p.dataset,evidencePackets.get(p.course_id));const i=packets.findIndex(x=>x.id===p.id);if(i>=0){if(data.kind!==KIND){message('This dataset is already imported; current decisions were preserved.');continue;}if(!confirm(`Replace the local review packet for ${p.course_id} with the imported bundle? Export first if you need both versions.`))continue;packets[i]=p;}else packets.push(p);}
 persist();refresh();
}
$('import-files').onclick=()=>$('files').click();
$('files').addEventListener('change',async()=>{for(const file of $('files').files){try{await importData(JSON.parse(await file.text()),file.name);}catch(e){message(`${file.name}: ${e.message}`,true);}}$('files').value='';});
function download(name,content,type){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([content],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export-json').onclick=()=>download(`eduground-review-${new Date().toISOString().slice(0,10)}.json`,JSON.stringify(bundle(),null,2),'application/json');
$('export-csv').onclick=()=>download('eduground-review.csv',exportCSV(packets),'text/csv;charset=utf-8');
$('clear').onclick=()=>{if(!confirm('Clear this browser workspace? Export JSON first to keep a copy. Original dataset files are not changed.'))return;packets=[];try{localStorage.removeItem(KEY);}catch{}refresh();message('Local workspace cleared.');};
for(const id of ['course','decision-filter','search'])$(id).addEventListener('input',()=>{page=0;refresh();});
$('previous').onclick=()=>{page--;refresh();};$('next').onclick=()=>{page++;refresh();};
$('demo').onclick=async()=>{await importData({dataset_id:'EXAMPLE — not research data',course_id:'EXAMPLE',samples:[{sample_id:'example-001',question:'What does LIFO mean for a stack?',reference_answer:'The last item added is the first item removed.',reference_contexts:['A stack uses last-in, first-out (LIFO) order. The last item pushed is the first item popped.'],source_ids:[],expected_locator:'Illustrative passage only',review_status:'draft'}]},'example.json');message('Illustrative example loaded. It is not part of an evaluation dataset.');};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
try{const saved=localStorage.getItem(KEY);if(saved){const b=await validateBundle(JSON.parse(saved));packets=b.packets;message('Restored browser draft. Export JSON regularly to back up decisions.');}}catch(e){message(`Could not restore browser draft: ${e.message}. Import your last exported bundle.`,true);}
refresh();
