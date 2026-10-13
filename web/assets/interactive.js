'use strict';
columnLabels.used_at='Đã dùng để học';
const bankChannelLabels={bidv:'BIDV',vcb:'Vietcombank',mb:'MB',vietinbank:'VietinBank',agribank:'Agribank',eximbank:'Eximbank',vpbank:'VPBank',abbank:'ABBank',sacombank:'Sacombank',shb:'SHB',momo:'MoMo',viettelpay:'Viettel Money / ViettelPay',payoo:'Payoo',vnpay:'VNPAY',zalopay:'ZaloPay',napas:'NAPAS',vnpt:'VNPT',shopee:'Shopee',shopeepay:'ShopeePay','buu dien':'Bưu điện'};

async function openInteractiveForm(kind, customerId='') {
  // Pin the revision to the opened form, even if another panel refreshes later.
  await api('/knowledge/revision');
  const stamp=knowledgeRevision;
  databaseSelected=null;databaseReturnFocus=document.activeElement;
  $('databaseRecordTitle').textContent=kind==='bank'?'Thêm tên ngân hàng / ví':'Thêm tên khác của khách hàng';
  const bank=kind==='bank';
  let fields='';
  if(bank){
    const {channels}=await api('/knowledge/bank-channels');
    fields='<label for="interactiveAlias">Tên xuất hiện trong tệp / tên sheet</label><input id="interactiveAlias" maxlength="100" required placeholder="Tên ngân hàng trong tệp"><label for="interactiveChannel">Ngân hàng / ví tương ứng</label><select id="interactiveChannel" required><option value="">Chọn ngân hàng / ví</option>'+channels.map(name=>'<option value="'+esc(name)+'">'+esc(bankChannelLabels[name]||name.toUpperCase())+'</option>').join('')+'</select><label for="interactiveNote">Ghi chú</label><input id="interactiveNote" maxlength="1000">';
  }else fields='<label for="interactiveCustomer">Mã khách hàng</label><input id="interactiveCustomer" pattern="[0-9]{6}" maxlength="6" required value="'+esc(customerId)+'" list="interactiveCustomers"><datalist id="interactiveCustomers"></datalist><label for="interactiveAlias">Tên khác của khách hàng</label><input id="interactiveAlias" maxlength="300" minlength="2" required>';
  $('databaseRecordBody').innerHTML='<form id="interactiveForm">'+fields+'<label class="check"><input type="checkbox" required><span>Tôi đã kiểm tra thông tin này.</span></label><button class="primary" type="submit">Xác nhận & lưu</button></form>';
  $('databaseModal').classList.remove('hidden');$(bank?'interactiveAlias':'interactiveCustomer').focus();
  if(!bank)customerAutocomplete('interactiveCustomer','interactiveCustomers');
  $('interactiveForm').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{
    const body=bank?{alias:$('interactiveAlias').value,channel_key:$('interactiveChannel').value,note:$('interactiveNote').value,confirmed:true}:{customer_id:$('interactiveCustomer').value,alias:$('interactiveAlias').value,confirmed:true};
    const result=await api(bank?'/knowledge/bank-aliases':'/knowledge/aliases',{method:'POST',knowledgeRevision:stamp,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    closeDatabaseRecord();toast(result.shared_customer_ids?.length>1?'Đã lưu. Tên này đang được dùng cho nhiều khách hàng.':'Đã lưu bí danh đã xác nhận.');
    databaseCustomerScope=null;databaseTemplateScope=null;databaseOffset=0;$('databaseQuery').value='';
    await loadDatabaseTables();$('databaseTable').value=bank?'bank_aliases':'customer_aliases';await loadDatabaseRows();
  })};
}

function bindInteractiveEditors(meta,row){
  const body=$('databaseRecordBody'),panel=document.createElement('div');panel.className='knowledge-tools';
  if(meta.name==='customers'){
    panel.innerHTML='<div class="buttons"><button id="interactiveAddName" class="small">Thêm bí danh</button><button id="interactiveViewNames" class="small">Xem các bí danh</button></div>';
    body.prepend(panel);
    $('interactiveAddName').onclick=()=>action($('interactiveAddName'),()=>openInteractiveForm('customer',row.id));
    $('interactiveViewNames').onclick=()=>{closeDatabaseRecord();databaseCustomerScope=row.id;databaseTemplateScope=null;databaseOffset=0;$('databaseQuery').value='';$('databaseTable').value='customer_aliases';loadDatabaseRows().catch(error=>toast(error.message,true))};
  }else if(meta.name==='customer_aliases'){
    panel.innerHTML='<div class="buttons"><button id="interactiveConfirmName" class="primary small">Xác nhận bí danh này</button><button id="interactiveRemoveName" class="danger small">Xóa bí danh</button></div>';
    body.prepend(panel);
    $('interactiveConfirmName').onclick=()=>action($('interactiveConfirmName'),async()=>{
      if(!await askConfirmation('Xác nhận bí danh?',row.alias+' thuộc khách hàng '+row.customer_id+'.','Xác nhận'))return;
      await api('/knowledge/aliases',{method:'POST',knowledgeRevision:row._revision,headers:{'Content-Type':'application/json'},body:JSON.stringify({customer_id:row.customer_id,alias:row.alias,confirmed:true})});closeDatabaseRecord();toast('Đã xác nhận bí danh.');await loadDatabaseTables();
    });
    $('interactiveRemoveName').onclick=()=>action($('interactiveRemoveName'),()=>interactiveRemove('/knowledge/aliases/'+row.id,row._revision,'Xóa bí danh '+row.alias+'?'));
  }else if(meta.name==='hard_negatives'){
    panel.innerHTML='<button id="interactiveRemoveBlock" class="danger small">Gỡ chặn cách ghép này</button>';body.prepend(panel);
    $('interactiveRemoveBlock').onclick=()=>action($('interactiveRemoveBlock'),()=>interactiveRemove('/knowledge/negatives/'+row.id,row._revision,'Gỡ chặn cách ghép với khách hàng '+row.customer_id+'?'));
  }else if(meta.name==='knowledge_changes'){
    panel.innerHTML='<p class="hint">'+(row.reverted_by?'Đã hoàn tác.':row.reversible?'Có thể hoàn tác.':'Không hỗ trợ hoàn tác.')+'</p>'+(row.reversible&&!row.reverted_by?'<button id="interactiveUndo" class="small danger">Hoàn tác thay đổi</button>':'');body.prepend(panel);
    if($('interactiveUndo'))$('interactiveUndo').onclick=()=>action($('interactiveUndo'),async()=>{
      if(!await askConfirmation('Hoàn tác thay đổi?','Khôi phục bí danh trước thay đổi số '+row.id+'.','Hoàn tác'))return;
      await api('/knowledge/changes/'+row.id+'/undo',{method:'POST',knowledgeRevision:row._revision});closeDatabaseRecord();toast('Đã hoàn tác thay đổi.');await loadDatabaseTables();
    });
  }
}

async function interactiveRemove(path,stamp,message){
  if(!await askConfirmation(message,'Thao tác này được ghi vào nhật ký kiến thức.','Xác nhận'))return;
  await api(path,{method:'DELETE',knowledgeRevision:stamp});closeDatabaseRecord();toast('Đã cập nhật kiến thức.');await loadDatabaseTables();
}
$('addCustomerAlias').onclick=()=>action($('addCustomerAlias'),()=>openInteractiveForm('customer'));
$('addBankAlias').onclick=()=>action($('addBankAlias'),()=>openInteractiveForm('bank'));
