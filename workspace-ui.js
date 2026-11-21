/* Dashboard settings are editable only on the direct loopback service. */
function workspaceBanner(w) {
  const mode=w.demo?'合成演示 · 用量、设备和评分均为示例':'本机工作区';
  return `<div class="workspace-note"><strong>${mode}</strong>　${w.consent?`已登记 ${w.devices.length} 台设备 · ${w.devices.filter(d=>d.status==='已盘点').length} 台有有效盘点`:'尚未读取设备。请在「设备与设置」授权首次盘点。'}${!document.documentElement.dataset.service?' · 静态只读':''}</div>`;
}
function input(label,name,value='',max=100) {return `<label>${label}<input name="${name}" maxlength="${max}" value="${esc(value)}" required></label>`;}
function form(op,fields,button,id='') {return `<form data-work="${op}" data-device="${esc(id)}">${fields}<button type="submit">${button}</button></form>`;}
function workspaceSettings(w) {
  const edit=!!document.documentElement.dataset.service;
  const devices=w.devices.map(d=>{
    const items=w.models.filter(m=>m.device===d.id), agents=Object.entries(d.agents);
    return `<section class="workspace-box"><h3>${esc(d.name)}</h3><p>${esc(d.status)} · ${d.alias?'SSH '+esc(d.alias):d.id==='local'?'面板所在设备':'示例节点'}${d.error?' · '+esc(d.error):''}</p>${agents.map(([a,st])=>`<span class="chip">${esc(a)} · ${esc(st)}</span>`).join(' ')}<p>${new Set(items.map(m=>m.model)).size} 个型号 · ${items.filter(m=>m.ready&&!m.disabled).length} 个可选档位</p>${edit?form('rename',input('设备显示名','name',d.name,64),'保存设备名',d.id):''}${edit&&!w.demo?form('scan','',w.consent?'重新盘点':'同意并盘点本机',d.id):''}<details><summary>型号与权限</summary>${items.map(m=>`<p>${esc(m.label)}　${esc(m.status)}</p>`).join('')||'<p>暂无型号记录</p>'}</details>${edit&&w.consent?`<details><summary>手动确认可用型号</summary><p>适用于未提供目录接口的 agent。仅登记你已验证可用的型号。</p>${form('manual',input('Agent','agent')+input('渠道','provider')+input('模型 ID','model')+input('努力档','effort','default')+'<label><input name="image" type="checkbox">支持图像输入</label>','确认并保存',d.id)}</details>`:''}</section>`;
  }).join('');
  return `<div class="sec-head"><h2>设备与设置</h2></div><div class="workspace-box"><h3>工作区名称</h3>${edit?form('title',input('面板名称','name',w.title,64),'保存名称'):esc(w.title)}</div><div class="sec-head"><h2>设备清单</h2></div><p>盘点读取 CLI、登录状态与型号目录。不会发起模型推理。已登记设备每小时复查；设备名可随时修改。</p><div class="workspace-grid">${devices}</div>${edit&&!w.demo&&w.consent?`<div class="workspace-box"><h3>添加远端设备</h3><p>先自行配置 SSH 密钥并确认主机指纹。授权后会在该设备执行本项目的只读盘点脚本。</p>${form('pair',input('设备显示名','name')+input('已信任的 SSH 别名','alias'),'授权并连接')}</div>`:''}`;
}
function workspacePolicy(w,routes){
  const opts=w.models.filter(m=>m.ready&&!m.disabled).map(m=>`<option value="${esc(m.key)}">${esc(m.label)}</option>`).join('');
  const edit=!!document.documentElement.dataset.service;
  return `<div class="sec-head"><h2>建议与手动选择</h2></div><div class="workspace-box"><p>按对应任务的分项指数排序，优先保留有额度的渠道，同分比较单任务成本。手动选择独立保存；此处不会派发任务。</p>${edit?form('pin',`<label>任务<select name="task">${routes.map(r=>`<option value="${r.index}">${esc(r.task)}</option>`)}</select></label><label>设备与型号<select name="key"><option value="">恢复自动建议</option>${opts}</select></label>`,'保存分工'):''}${edit&&w.models.length?`<details><summary>禁用或恢复候选</summary>${form('toggle',`<label>候选<select name="key">${w.models.map(m=>`<option value="${esc(m.key)}">${esc(m.label)} · ${esc(m.status)}</option>`)}</select></label>`,'切换禁用状态')}</details>`:''}<details open><summary>各项依据</summary>${routes.map(r=>`<p><strong>${esc(r.task)}</strong>　${esc(r.preferred)}　<span class="muted">${esc(r.reason)}</span></p>`).join('')}</details></div>`;
}
function workspaceRatings(w){const s=w.scoreInfo, edit=!!document.documentElement.dataset.service;
  return `<div class="workspace-box"><h3>评分来源</h3><p>${w.demo?'合成示例分，供比较界面效果。':`<a href="https://artificialanalysis.ai/" target="_blank" rel="noreferrer">Artificial Analysis</a> · ${s.updated?esc(new Date(s.updated*1000).toLocaleString())+' · v'+esc(s.version):'尚未取得评分'}${s.stale&&s.updated?' · 缓存已过期':''}${s.error?' · 更新失败：'+esc(s.error):''}`}</p>${!w.demo?`<p>${s.enabled?'自动更新已开启，每 24 小时检查。':'自动更新未开启。'}${s.hasKey?' 服务已配置 API key。':' 启动服务前设置 AA_API_KEY 可启用更新。'} 同步无需发送设备清单。</p>${edit&&s.hasKey?form('sync','',s.enabled?'立即刷新':'启用并同步'):''}${edit&&s.enabled?form('stopSync','','关闭自动更新'):''}`:''}</div>`;}
document.addEventListener('submit',async e=>{
  const f=e.target.closest('form[data-work]');if(!f)return;e.preventDefault();
  const msg=document.getElementById('work-msg');msg.hidden=false;msg.textContent='处理中…';const button=f.querySelector('button');button.disabled=true;
  try{const body=Object.fromEntries(new FormData(f));body.op=f.dataset.work;body.device=f.dataset.device||'local';if(body.image)body.image=true;
    const r=await fetch('./api/workspace',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Action':'edit'},body:JSON.stringify(body)});
    if(!r.ok)throw Error((await r.json().catch(()=>({}))).error||'设置仅允许在本机直连页面修改');await load(false);msg.textContent='已保存。';
  }catch(err){msg.textContent=err.message;}finally{button.disabled=false;}
});
