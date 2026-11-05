export const KIND = 'eduground-human-review-v1';
export const decisions = ['Pending', 'Accept', 'Flag', 'Reject'];
export async function digest(value) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(value)));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}
export function validateDataset(data) {
  if (!data || !Array.isArray(data.samples) || !data.samples.length) throw Error('Expected a non-empty samples array.');
  if (data.source_manifest !== undefined && (!Array.isArray(data.source_manifest) || !data.source_manifest.every(s => s && typeof s === 'object'))) throw Error('Source manifest must be an array of source records.');
  const ids = new Set();
  for (const s of data.samples) {
    if (!s || typeof s.sample_id !== 'string' || !s.sample_id.trim() || ids.has(s.sample_id)) throw Error('Question IDs must be non-empty and unique within a dataset.');
    ids.add(s.sample_id);
    const evidence = data.schema_version === 2 ? s.evidence?.map(e => e?.quote) : s.reference_contexts;
    if (typeof s.question !== 'string') throw Error(`Invalid question for ${s.sample_id}.`);
    if (data.schema_version === 2) {
      if (typeof s.answerable !== 'boolean' || !Array.isArray(s.evidence) || (s.reference_answer !== null && s.reference_answer !== undefined && typeof s.reference_answer !== 'string')) throw Error(`Invalid v2 answerability for ${s.sample_id}.`);
      if (s.answerable && (!s.reference_answer?.trim() || !s.evidence.length)) throw Error(`Answerable item lacks reference or evidence for ${s.sample_id}.`);
      if (!s.answerable && s.evidence.length) throw Error(`Unanswerable item has evidence for ${s.sample_id}.`);
      if (!s.evidence.every(e => e && typeof e.source_id === 'string' && e.source_id && typeof e.chunk_id === 'string' && e.chunk_id && typeof e.quote === 'string' && e.quote.trim())) throw Error(`Invalid v2 evidence for ${s.sample_id}.`);
    } else if (typeof s.reference_answer !== 'string' || !Array.isArray(evidence) || !evidence.length || !evidence.every(x => typeof x === 'string' && x.trim())) throw Error(`Invalid reference/evidence for ${s.sample_id}.`);
  }
}
export async function packetFromDataset(data, filename) {
  validateDataset(data);
  const hash = await digest(data);
  return {id:hash, dataset_hash:hash, course_id:String(data.course_id || data.course_code || data.dataset_id || filename.replace(/\.json$/i,'')), filename, dataset:data, reviews:[], history:[]};
}
export function matchEvidencePacket(dataset, packet) {
  if (dataset.schema_version!==2 || !packet || packet.course_id!==dataset.course_id || packet.notebook_id!==dataset.notebook_id || !Array.isArray(packet.sources)) throw Error('Evidence packet does not match the v2 course and notebook.');
  const sources=new Map();
  for(const source of packet.sources){
    if(!source || typeof source.source_id!=='string' || sources.has(source.source_id) || !Array.isArray(source.chunks)) throw Error('Evidence packet has duplicate or invalid sources.');
    sources.set(source.source_id,source);
  }
  for(const listed of dataset.source_manifest){
    const source=sources.get(listed.source_id);
    if(!source || source.checksum_sha256!==listed.sha256 || (listed.version_number!=null && source.version_number!==listed.version_number)) throw Error(`Evidence packet source ${listed.source_id} differs from dataset manifest.`);
  }
  const byChunk=new Map();
  for(const source of sources.values())for(const chunk of source.chunks){
    if(!chunk || typeof chunk.chunk_id!=='string' || typeof chunk.text!=='string' || byChunk.has(chunk.chunk_id)) throw Error('Evidence packet has duplicate or invalid chunks.');
    byChunk.set(chunk.chunk_id,{...chunk,source_id:source.source_id});
  }
  return byChunk;
}
export function checkReviewEvidence(sample, review, byChunk) {
  if(!sample.answerable)return;
  if(!Array.isArray(review.supporting_evidence) || review.supporting_evidence.length!==sample.evidence.length) throw Error('Source-bound evidence is incomplete.');
  review.supporting_evidence.forEach((entry,i)=>{
    const original=sample.evidence[i],chunk=byChunk.get(entry.chunk_id);
    if(!chunk || chunk.source_id!==entry.source_id || entry.chunk_id!==original.chunk_id || entry.source_id!==original.source_id) throw Error(`Evidence ${i+1} points to the wrong indexed chunk.`);
    const normalize=text=>text.split(/\s+/).filter(Boolean).join(' ');
    if(!normalize(chunk.text).includes(normalize(entry.quote))) throw Error(`Evidence ${i+1} quote is not in its complete indexed chunk.`);
  });
}
export function initialFields(sample, review) {
  if (review) return {...review};
  return {sample_id:sample.sample_id, question:sample.question, reference_answer:sample.reference_answer || '', supporting_passages:(sample.evidence ? sample.evidence.map(e=>e.quote) : sample.reference_contexts).join('\n\n'),
    ...(sample.evidence ? {supporting_evidence:sample.evidence.map(e=>({source_id:e.source_id,chunk_id:e.chunk_id,quote:e.quote}))} : {}),
    required_changes:'', reviewer:'', decision:'Pending', reviewed_at_utc:null, evidence_checked:false};
}
export function reviewContent(review) {
  const content={question:review.question,reference_answer:review.reference_answer,supporting_passages:review.supporting_passages};
  if (review.supporting_evidence !== undefined) content.supporting_evidence=review.supporting_evidence;
  return content;
}
export async function makeReview(fields, previous) {
  if (!decisions.includes(fields.decision)) throw Error('Choose a valid decision.');
  if (!fields.reviewer.trim()) throw Error('Enter your reviewer name.');
  if (!fields.question.trim()) throw Error('Question is required.');
  if (fields.answerable !== false && (!fields.reference_answer.trim() || !fields.supporting_passages.trim())) throw Error('Reference and supporting passages are required for answerable questions.');
  if (fields.supporting_evidence !== undefined && (!Array.isArray(fields.supporting_evidence) || !fields.supporting_evidence.every(e=>e && e.source_id && e.chunk_id && typeof e.quote==='string' && e.quote.trim()))) throw Error('Each source-bound passage needs its source, chunk and quote.');
  if (fields.supporting_evidence !== undefined && fields.supporting_passages !== fields.supporting_evidence.map(e=>e.quote).join('\n\n')) throw Error('Displayed passages differ from source-bound evidence.');
  if (fields.answerable === false && (fields.supporting_passages.trim() || fields.supporting_evidence?.length)) throw Error('Unanswerable questions cannot carry supporting evidence.');
  if (fields.decision === 'Accept' && !fields.evidence_checked) throw Error('Check the evidence confirmation before accepting.');
  if (['Reject','Flag'].includes(fields.decision) && !fields.required_changes.trim()) throw Error('Explain the rejection or flag in Required changes.');
  const {answerable,...saved}=fields;
  return {...saved, revision:(previous?.revision || 0)+1, content_hash:await digest(reviewContent(fields)), reviewed_at_utc:new Date().toISOString()};
}
export async function validateBundle(data) {
  if (data?.kind !== KIND || !Array.isArray(data.packets)) throw Error('Unrecognised review bundle.');
  const seen = new Set();
  for (const p of data.packets) {
    validateDataset(p.dataset);
    if (p.id !== await digest(p.dataset) || p.dataset_hash !== p.id || seen.has(p.id)) throw Error('Dataset hash mismatch or duplicate packet.');
    seen.add(p.id);
    if (typeof p.course_id !== 'string' || !p.course_id.trim() || !Array.isArray(p.reviews) || !Array.isArray(p.history)) throw Error('Invalid course or review history.');
    if (p.dataset.schema_version === 2 && p.course_id !== p.dataset.course_id) throw Error('Course ID differs from the v2 dataset.');
    const ids = new Set(p.dataset.samples.map(s => s.sample_id)), reviewIds = new Set();
    for (const r of [...p.reviews,...p.history]) {
      if (!ids.has(r.sample_id) || !decisions.includes(r.decision) || typeof r.evidence_checked !== 'boolean' || !['question','reference_answer','supporting_passages','required_changes','reviewer'].every(k => typeof r[k] === 'string') || !r.reviewer.trim() || !Number.isInteger(r.revision) || r.revision < 1 || !Number.isFinite(Date.parse(r.reviewed_at_utc))) throw Error('Invalid review record.');
      if (r.content_hash !== await digest(reviewContent(r))) throw Error('Review content hash mismatch.');
      const sample=p.dataset.samples.find(s=>s.sample_id===r.sample_id);
      const answerable=p.dataset.schema_version===2 ? sample.answerable : true;
      if (p.dataset.schema_version===2 && r.supporting_evidence !== undefined && (!Array.isArray(r.supporting_evidence) || r.supporting_evidence.length!==sample.evidence.length || !r.supporting_evidence.every((e,i)=>e?.source_id===sample.evidence[i].source_id && e?.chunk_id===sample.evidence[i].chunk_id && typeof e.quote==='string' && e.quote.trim()))) throw Error('Review evidence must retain source and chunk IDs.');
      if (r.supporting_evidence !== undefined && r.supporting_passages !== r.supporting_evidence.map(e=>e.quote).join('\n\n')) throw Error('Displayed passages differ from source-bound evidence.');
      if (!answerable && (r.supporting_passages.trim() || r.supporting_evidence?.length)) throw Error('Unanswerable review cannot have supporting evidence.');
      if (r.decision === 'Accept' && (!r.evidence_checked || !r.question.trim() || (answerable && (!r.reference_answer.trim() || !r.supporting_passages.trim())))) throw Error('Accepted review lacks evidence confirmation or content.');
      if (['Reject','Flag'].includes(r.decision) && !r.required_changes.trim()) throw Error('Flag/reject record lacks explanation.');
    }
    for (const r of p.reviews) {if (reviewIds.has(r.sample_id)) throw Error('Duplicate current review.'); reviewIds.add(r.sample_id);}
  }
  return data;
}
export function csvCell(value) {
  let s = String(value ?? '');
  if (/^[\s]*[=+\-@]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"','""') + '"';
}
export function exportCSV(packets) {
  const rows = [['Course','Question ID','Question','Decision','Corrected reference','Supporting passages','Required changes','Reviewer','Date UTC','Dataset hash','Review revision']];
  for (const p of packets) for (const s of p.dataset.samples) {
    const r = initialFields(s,p.reviews.find(x => x.sample_id === s.sample_id));
    rows.push([p.course_id,s.sample_id,r.question,r.decision,r.reference_answer,r.supporting_passages,r.required_changes,r.reviewer,r.reviewed_at_utc,p.dataset_hash,r.revision || 0]);
  }
  return '\ufeff' + rows.map(r => r.map(csvCell).join(',')).join('\r\n');
}
