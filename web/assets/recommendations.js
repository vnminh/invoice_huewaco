'use strict';
let candidateGroupRequest=0,candidateGroupState=null;

function candidateGroupSummary(row){
  if(row.decision==='auto_accept'&&!row.candidate_group)return '';
  const group=row.candidate_group;
  return '<details id="candidateGroupDetails" class="innerdetails"><summary>Nhóm khách hàng cần kiểm tra'+(group?.total?' · '+group.total.toLocaleString('vi-VN')+' khách hàng':'')+'</summary><div id="candidateGroupPanel" aria-live="polite"></div></details>';
}

function renderCandidateGroup(row,group){
  const panel=$('candidateGroupPanel');if(!panel)return;
  const members=group.members||[],query=candidateGroupState?.q||'';
  candidateGroupState={...candidateGroupState,rowId:row.id,items:members,offset:group.offset||0,total:group.total||0,limit:group.limit||20,q:query};
  $('candidateGroupDetails').querySelector('summary').textContent='Nhóm khách hàng cần kiểm tra · '+(group.total||0).toLocaleString('vi-VN')+' khách hàng';
  panel.innerHTML=    '<form id="candidateGroupSearch" class="searchline"><div><label for="candidateGroupQuery">Tìm trong nhóm</label><input id="candidateGroupQuery" maxlength="100" value="'+esc(query)+'" placeholder="Tên hoặc mã khách hàng"></div><button class="small" type="submit">Tìm</button></form>'+
    '<div class="tablewrap"><table><thead><tr><th>IDKH</th><th>Khách hàng</th><th>Lịch sử</th><th></th></tr></thead><tbody>'+
    (members.length?members.map(item=>'<tr><td>'+esc(item.customer_id)+'</td><td>'+esc(item.customer_name||'Chưa có tên')+(!item.group_only?'<div class="rowmeta">'+(item.sources||[]).map(esc).join('; ')+'</div>':'')+'</td><td>'+(item.seen_count?Number(item.seen_count).toLocaleString('vi-VN')+' lượt trong nhóm lịch sử':'Gợi ý từ nội dung hiện tại')+(item.collection_count?'<div class="rowmeta">Có trong danh sách thu hộ</div>':'')+'</td><td>'+(row.status==='pending'?'<button type="button" class="small" data-group-customer="'+esc(item.customer_id)+'">Chọn</button>':'')+'</td></tr>').join(''):'<tr><td colspan="4" class="empty">'+(query?'Không có khách hàng phù hợp trong nhóm.':'Chưa có lịch sử liên quan để giới hạn nhóm khách hàng.')+'</td></tr>')+
    '</tbody></table></div><div class="pager"><span class="sub">'+(members.length?(group.offset+1)+'–'+(group.offset+members.length)+' / '+group.total.toLocaleString('vi-VN'):'0 khách hàng')+'</span><div class="buttons"><button type="button" class="small" data-group-page="prev" '+(!group.offset?'disabled':'')+'>← Trước</button><button type="button" class="small" data-group-page="next" '+(group.offset+members.length>=group.total?'disabled':'')+'>Tiếp →</button></div></div>';
  $('candidateGroupSearch').onsubmit=event=>{event.preventDefault();action(event.submitter,()=>loadCandidateGroup(row,0,$('candidateGroupQuery').value.trim()))};
  panel.onclick=event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.groupCustomer&&row.status==='pending'){
      const item=candidateGroupState.items.find(item=>item.customer_id===button.dataset.groupCustomer);
      if(item&&$('correctId')){$('correctId').value=item.customer_id;$('correctName').value=item.customer_name||'';$('correctId').focus()}
    }else if(button.dataset.groupPage){
      const state=candidateGroupState,next=button.dataset.groupPage==='next'?state.offset+state.limit:Math.max(0,state.offset-state.limit);
      action(button,()=>loadCandidateGroup(row,next,state.q));
    }
  };
}

async function loadCandidateGroup(row,offset=0,q=''){
  const request=++candidateGroupRequest;
  const params=new URLSearchParams({offset,limit:20,q});
  const group=await api('/transactions/'+row.id+'/candidates?'+params);
  if(request!==candidateGroupRequest||selected?.id!==row.id||!$('candidateGroupPanel'))return;
  candidateGroupState={rowId:row.id,q};renderCandidateGroup(row,group);
}

function bindCandidateGroup(row){
  const details=$('candidateGroupDetails');if(!details)return;
  candidateGroupRequest++;candidateGroupState={rowId:row.id,q:''};
  let loaded=false;
  if(row.candidate_group)renderCandidateGroup(row,row.candidate_group);
  const load=()=>{if(details.open&&!loaded){loaded=true;loadCandidateGroup(row).catch(error=>{loaded=false;toast(error.message,true)})}};
  details.addEventListener('toggle',load);load();
}
