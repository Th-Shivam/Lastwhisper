export type Segment = {id:string;position:number;start_seconds:number;end_seconds:number;text:string}
export type Recording = { id:string; title:string; original_filename:string; mime_type:string; file_size:number; duration_seconds:number|null; created_at:string; transcription_status:string; transcript_text?:string|null; summary?:string|null; error_message:string|null; transcription_language:string|null; selected_language:string|null; transcription_progress:number; extraction_status:string; extracted_memories?:string|null; segments?:Segment[] }
export type Stats = {total_recordings:number; transcribed_recordings:number; total_duration_seconds:number; saved_memories:number}
export type SearchResult = {chunk_id:string; recording_id:string; recording_title:string; recording_date:string; segment_id:string; segment_ids:string[]; start_seconds:number; end_seconds:number; text:string; similarity:number}
export type SearchResponse = {results:SearchResult[]; minimum_similarity:number; score_explanation:string}
export type SavedMemory = {id:string; recording_id:string; segment_id:string; title:string|null; note:string|null; created_at:string; updated_at:string; source_text:string; source_start_seconds:number; source_end_seconds:number; recording_title:string; recording_date:string}
export type IndexStatus = {available:boolean; model_status:string; model_loaded:boolean; configured_model:string; configured_device:string; total_chunks:number; indexed_recordings:number; pending_recordings:number; failed_recordings:number; rebuilding:boolean; error:string|null}
export type IndexRebuild = {indexed_chunks:number; indexed_recordings:number; empty_recordings:number; failed_recordings:number; errors:{recording_id:string;error:string}[]}
const BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api').replace(/\/+$/, '')
const parseError = (data: unknown) => {
  if (!data || typeof data !== 'object' || !('detail' in data)) return null
  const detail = (data as {detail?:unknown}).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => typeof item === 'object' && item && 'msg' in item ? String(item.msg) : '').filter(Boolean).join(' ')
  return null
}
async function request<T>(path:string, options?:RequestInit, timeoutMs=30000):Promise<T>{
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${BASE}${path}`, {...options, signal: options?.signal ?? controller.signal})
    if(!res.ok){const data=await res.json().catch(()=>null);throw new Error(parseError(data)||'Something went wrong. Please try again.')}
    return res.status===204 ? undefined as T : res.json()
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw Object.assign(new Error('The local API did not respond in time.'), { cause: error })
    throw error
  } finally { window.clearTimeout(timeout) }
}
export const api={
  health:()=>request<{status:string}>('/health'),
  recordings:()=>request<Recording[]>('/recordings'),
  stats:()=>request<Stats>('/recordings/stats'),
  get:(id:string)=>request<Recording>(`/recordings/${id}`),
  audioUrl:(id:string)=>`${BASE}/recordings/${id}/audio`,
  rename:(id:string,title:string)=>request<Recording>(`/recordings/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})}),
  remove:(id:string)=>request<void>(`/recordings/${id}`,{method:'DELETE'}),
  transcribe:(id:string,language?:string,replace_existing=false)=>request<Recording>(`/recordings/${id}/transcribe`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...(language?{language}:{}),replace_existing})}),
  cancel:(id:string)=>request<Recording>(`/recordings/${id}/transcribe/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),
  upload:(file:File,onProgress:(n:number)=>void)=>new Promise<Recording>((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',`${BASE}/recordings/import`);xhr.timeout=120000;xhr.upload.onprogress=e=>e.lengthComputable&&onProgress(Math.round(e.loaded/e.total*100));xhr.onload=()=>{let data:unknown;try{data=xhr.responseText?JSON.parse(xhr.responseText):null}catch{reject(new Error('The local API returned an invalid upload response.'));return}if(xhr.status>=200&&xhr.status<300&&data)resolve(data as Recording);else reject(new Error(parseError(data)||'Upload failed.'))};xhr.onerror=()=>reject(new Error('Unable to reach the local API.'));xhr.ontimeout=()=>reject(new Error('The upload timed out.'));xhr.onabort=()=>reject(new Error('The upload was cancelled.'));const form=new FormData();form.append('file',file);xhr.send(form)}),
  search:(query:string,opts?:{recording_id?:string;limit?:number})=>request<SearchResponse>('/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,...opts})},30*60*1000),
  searchStatus:()=>request<IndexStatus>('/search/status'),
  rebuildIndex:()=>request<IndexRebuild>('/search/rebuild',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})},30*60*1000),
  memories:()=>request<SavedMemory[]>('/memories'),
  saveMemory:(data:{segment_id?:string;search_chunk_id?:string;title?:string|null;note?:string|null})=>request<SavedMemory>('/memories',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
  updateMemory:(id:string,data:{title?:string|null;note?:string|null})=>request<SavedMemory>(`/memories/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),
  deleteMemory:(id:string)=>request<void>(`/memories/${id}`,{method:'DELETE'}),
}
