import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const collection={
 list:()=>api('/collection/batches'),
 get:id=>api('/collection/batches/'+id),
 create:data=>api('/collection/batches',{method:'POST',body:JSON.stringify(data)}),
 device:id=>api('/collection/devices/'+id),
 endSeq:(id,end_seq)=>api('/collection/devices/'+id+'/end-seq',{method:'POST',body:JSON.stringify({end_seq})}),
 upload:(id,records)=>api('/collection/devices/'+id+'/records',{method:'POST',body:JSON.stringify({records})}),
 seal:id=>api('/collection/batches/'+id+'/seal',{method:'POST'}),
 resolve:(id,keep,note)=>api('/collection/conflicts/'+id+'/resolve',{method:'POST',body:JSON.stringify({keep,note})}),
};
