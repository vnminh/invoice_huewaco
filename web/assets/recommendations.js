'use strict';
let candidateGroupRequest=0,candidateGroupState=null;

function candidateGroupSummary(row){
  if(row.decision==='auto_accept'&&!row.candidate_group&&!row.recommendations?.length)return '';
  const group=row.candidate_group;
  return '<details id="candidateGroupDetails" class="innerdetails"><summary>Nhóm khách hàng cần kiểm tra'+(group?.total?' · '+group.total.toLocaleString('vi-VN')+' khách hàng':'')+'</summary><div id="candidateGroupPanel" aria-live="polite"></div></details>';
}

function candidateExample(row,example){
  const collection=example.kind==='collection';
  const meta=[example.channel&&example.channel.toUpperCase(),paymentLabels[example.payment_mode]||'',example.example_date&&transferDateText(example.example_date)].filter(Boolean);
  const source=[example.source_file,example.source_row!=null?'Dòng '+example.source_row:''].filter(Boolean).join(' · ');
  return '<section class="candidate-example"><div class="candidate-example-title">'+(collection?'Bố cục thu hộ tổng hợp':'Nội dung đã xác nhận')+'</div>'+
    '<div class="rowmeta">'+meta.map(esc).join(' · ')+'</div><div class="rawfull">'+esc(example.content)+'</div>'+
    (source?'<div class="rowmeta candidate-source">'+esc(source)+'</div>':'')+
    '<div class="candidate-reasons">'+(example.reasons||[]).map(esc).join(' · ')+'</div>'+
    (collection?'<div class="rowmeta">Danh sách thu hộ chung; chưa có nội dung riêng của khách hàng trong mẫu này.</div>':
      '<details class="candidate-comparison"><summary>So với giao dịch này</summary><label>Nội dung đang đối soát</label><div class="rawfull">'+esc(row.raw)+'</div>'+
      (example.numeric_comparison?.ordered_comparison?.length?orderedEvidence(example):'<div class="rowmeta">Không có số trong nội dung hiện tại để đối chiếu.</div>')+'</details>')+'</section>';
}

function candidateCard(row,item){
  const examples=item.examples;
  const evidence=examples===undefined?'<div class="rowmeta">Đang tải mẫu đã học…</div>':
    examples.length?candidateExample(row,examples[0])+(examples.length>1?'<details class="candidate-more"><summary>Xem thêm '+(examples.length-1)+' mẫu</summary>'+examples.slice(1).map(example=>candidateExample(row,example)).join('')+'</details>':''):
      '<div class="rowmeta">Chưa có mẫu nội dung đã học của khách hàng này.</div>';
  return '<article class="candidate-card"><div class="candidate-card-head"><div><strong>'+esc(item.customer_id)+' · '+esc(item.customer_name||'Chưa có tên')+'</strong>'+
    '<div class="rowmeta">'+(item.sources||[]).map(esc).join(' · ')+'</div></div>'+
    (row.status==='pending'?'<button type="button" class="small" data-group-customer="'+esc(item.customer_id)+'">Chọn khách hàng</button>':'')+'</div>'+evidence+'</article>';
}

function renderCandidateGroup(row,group){
  const panel=$('candidateGroupPanel');if(!panel)return;
  const members=group.members||[],query=candidateGroupState?.q||'';
  candidateGroupState={...candidateGroupState,rowId:row.id,items:members,offset:group.offset||0,total:group.total||0,limit:group.limit||20,q:query};
  $('candidateGroupDetails').querySelector('summary').textContent='Nhóm khách hàng cần kiểm tra · '+(group.total||0).toLocaleString('vi-VN')+' khách hàng';
  panel.innerHTML=    '<form id="candidateGroupSearch" class="searchline"><div><label for="candidateGroupQuery">Tìm trong nhóm</label><input id="candidateGroupQuery" maxlength="100" value="'+esc(query)+'" placeholder="Tên hoặc mã khách hàng"></div><button class="small" type="submit">Tìm</button></form>'+
    '<div class="candidate-list">'+(members.length?members.map(item=>candidateCard(row,item)).join(''):'<div class="empty">'+(query?'Không có khách hàng phù hợp trong nhóm.':'Chưa có lịch sử liên quan để giới hạn nhóm khách hàng.')+'</div>')+
    '</div><div class="pager"><span class="sub">'+(members.length?(group.offset+1)+'–'+(group.offset+members.length)+' / '+group.total.toLocaleString('vi-VN'):'0 khách hàng')+'</span><div class="buttons"><button type="button" class="small" data-group-page="prev" '+(!group.offset?'disabled':'')+'>← Trước</button><button type="button" class="small" data-group-page="next" '+(group.offset+members.length>=group.total?'disabled':'')+'>Tiếp →</button></div></div>';
  $('candidateGroupSearch').onsubmit=event=>{event.preventDefault();action(event.submitter,()=>loadCandidateGroup(row,0,$('candidateGroupQuery').value.trim()))};
  panel.onclick=event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.groupCustomer&&row.status==='pending'){
      const item=candidateGroupState.items.find(item=>item.customer_id===button.dataset.groupCustomer);
      if(item&&$('correctId')){$('correctId').value=item.customer_id;$('correctName').value=item.customer_name||'';$('correctId').focus();toast('Đã chọn khách hàng '+item.customer_id)}
    }else if(button.dataset.groupPage){
      const state=candidateGroupState,next=button.dataset.groupPage==='next'?state.offset+state.limit:Math.max(0,state.offset-state.limit);
      action(button,()=>loadCandidateGroup(row,next,state.q));
    }
  };
}

async function loadCandidateGroup(row,offset=0,q=''){
  const request=++candidateGroupRequest;
  const params=new URLSearchParams({offset,limit:20,q});
  const panel=$('candidateGroupPanel');panel?.setAttribute('aria-busy','true');
  try{
    const group=await api('/transactions/'+row.id+'/candidates?'+params);
    if(request!==candidateGroupRequest||selected?.id!==row.id||!$('candidateGroupPanel'))return;
    candidateGroupState={rowId:row.id,q};renderCandidateGroup(row,group);
  }catch(error){
    if(request!==candidateGroupRequest||selected?.id!==row.id)return;
    if(panel){panel.innerHTML='<p role="alert">Chưa tải được mẫu đã học.</p><button type="button" class="small" data-retry-candidates>Thử lại</button>';
      panel.querySelector('[data-retry-candidates]').onclick=event=>action(event.currentTarget,()=>loadCandidateGroup(row,offset,q));}
    throw error;
  }finally{if(request===candidateGroupRequest)panel?.removeAttribute('aria-busy')}
}

function bindCandidateGroup(row){
  const details=$('candidateGroupDetails');if(!details)return;
  candidateGroupRequest++;candidateGroupState={rowId:row.id,q:''};
  let loaded=false;
  if(row.candidate_group)renderCandidateGroup(row,row.candidate_group);
  const load=()=>{if(details.open&&!loaded){loaded=true;loadCandidateGroup(row).catch(error=>{loaded=false;toast(error.message,true)})}};
  for(const button of $('reviewBody').querySelectorAll('[data-candidate-evidence]'))button.onclick=()=>{
    loaded=true;details.open=true;
    action(button,async()=>{try{await loadCandidateGroup(row,0,button.dataset.candidateEvidence);details.scrollIntoView({block:'start'})}catch(error){loaded=false;throw error}});
  };
  details.addEventListener('toggle',load);load();
}
