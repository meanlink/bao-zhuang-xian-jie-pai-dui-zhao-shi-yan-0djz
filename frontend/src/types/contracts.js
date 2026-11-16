/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,status:'open'|'waiting'|'conflict'|'sealed',dataset_id:number|null,device_count:number,confirmed_count:number,buffered_count:number,missing_count:number,open_conflict_count:number,devices:CollectionDevice[]}} CollectionBatch */
/** @typedef {{id:number,batch_id:number,code:string,label:string,end_seq:number|null,confirmed_seq:number,status:'receiving'|'waiting'|'blocked'|'done',buffered_seqs:number[],missing_seqs:number[],next_expected:number,open_conflict_count:number,conflicts:CollectionConflict[],records?:CollectionRecordEntry[],record_count?:number}} CollectionDevice */
/** @typedef {{id:number,device_id:number,seq:number,existing_payload:Object,incoming_payload:Object,state:string,note:string}} CollectionConflict */
/** @typedef {{seq:number,state:'buffered'|'confirmed',payload:Object,received_at:number,confirmed_at:number|null}} CollectionRecordEntry */
export {};
