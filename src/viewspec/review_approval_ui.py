"""Studio's rendered current/proposed comparison, inside the checked chrome."""

APPROVAL_HTML = """<section id="convergence" class="approval-review" hidden aria-labelledby="approval-title">
  <div class="approval-header">
    <div><span class="eyebrow">Review the proposed change</span>
      <h2 id="approval-title" tabindex="-1">Compare before approving</h2>
      <p id="convergence-summary"></p></div>
    <div class="approval-actions">
      <button id="reject-convergence" class="secondary" type="button">Reject</button>
      <button id="approve-convergence" class="primary" type="button" disabled>Approve change</button>
    </div>
  </div>
  <div class="approval-tools">
    <label id="approval-surface-label" hidden>Rendering <select id="approval-surface" aria-label="Rendering"></select></label>
    <label id="approval-state-label" hidden>State <select id="approval-state" aria-label="Comparison state"><option value="initial">Initial state</option><option value="checkpoint">Active checkpoint</option></select></label>
    <label>Size <select id="approval-size" aria-label="Size"><option value="fit">Fit both</option><option value="full">100% · scroll to inspect</option></select></label>
    <span id="approval-context"></span>
  </div>
  <p id="approval-status" role="status" aria-live="polite">Preparing proposed preview…</p>
  <div id="approval-window" class="approval-window">
    <div id="approval-labels" class="approval-labels"><strong>Current interface</strong><strong>Proposed interface</strong></div>
    <div id="approval-fit" class="approval-fit"><div id="approval-stage" class="approval-stage"></div></div>
  </div>
  <details class="approval-details"><summary>What changes</summary>
    <div id="convergence-diff" class="change-list"></div><p id="convergence-proof" class="proof-result"></p>
  </details>
</section>
"""

APPROVAL_CSS = """
.approval-review{width:100%;min-width:0;align-self:start}
.approval-header{position:sticky;top:0;z-index:2;background:#0b0e13;display:flex;justify-content:space-between;align-items:center;gap:20px;padding:10px 0}
.approval-header h2{margin:4px 0;font-size:22px;line-height:1.25}
.approval-header p{margin:5px 0;color:var(--muted);font-size:12px}
.approval-actions{display:flex;gap:8px;flex-shrink:0}
.approval-tools{display:flex;align-items:center;flex-wrap:wrap;gap:16px;margin:18px 0 6px}
.approval-tools label{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}
.approval-tools select{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text)}
#approval-context{color:var(--muted);font-size:12px}
#approval-status{margin:8px 0 16px;color:var(--mint);font-size:12px;min-height:18px}
.approval-labels{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:0 auto 8px;font-size:13px}
.approval-labels strong:last-child{color:var(--mint)}
.approval-window{width:100%;overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel);overscroll-behavior:contain}
.approval-fit{position:relative;margin:0 auto}
.approval-stage{position:absolute;top:0;left:0;display:flex;gap:16px;width:max-content;transform-origin:top left}
.approval-stage iframe{display:block;flex-shrink:0;border:0;border-radius:0;background:white}
.approval-details{margin-top:16px;border-top:1px solid var(--line);padding:14px 0}
.approval-details summary{cursor:pointer;color:var(--muted);font-size:12px}
.approval-details .change-list{margin-top:12px}
@media(max-width:860px){.canvas:has(.approval-review:not([hidden])){height:auto;min-height:58vh}.approval-header{flex-wrap:wrap;gap:12px}.approval-header h2{font-size:20px}.approval-actions{width:100%}.approval-actions button{flex:1}.approval-tools{gap:10px}.approval-labels{font-size:12px}}
"""

# Runs within the chrome closure. Proposal frames have their own nonce and are
# deliberately excluded from the ordinary comment/selection message handler.
APPROVAL_JS = r"""
const approvalStage=document.getElementById('approval-stage'),approvalFit=document.getElementById('approval-fit'),
  approvalLabels=document.getElementById('approval-labels'),
  approvalWindow=document.getElementById('approval-window'),approvalSurface=document.getElementById('approval-surface'),
  approvalSize=document.getElementById('approval-size'),approvalStatus=document.getElementById('approval-status'),
  approvalState=document.getElementById('approval-state'),
  approvalContext=document.getElementById('approval-context'),normalStage=fitShell.closest('.stage');
let approvalId=null,approvalKey=null,approvalRendered=null,approvalFrames=[],approvalObservations=new Map(),
  approvalLoaded=new Set(),approvalConfirmed=false,approvalConfirming=false,approvalFailed=false,
  approvalBusy=false,approvalGeneration=0,approvalTimeout=null,approvalRestore=null,approvalReplay=null,approvalCheckpoint=null,approvalReplayed=new Set();
const fitApproval=()=>{
  if(convergence.hidden||!approvalFrames.length)return;
  const dimensions={mobile:[390,844],tablet:[768,1024],desktop:[1440,1000]}[document.documentElement.dataset.studioViewport];
  if(!dimensions)return;
  approvalFrames.forEach(item=>{item.width=dimensions[0];item.height=dimensions[1];});
  const width=dimensions[0]*2+16,height=dimensions[1],scale=approvalSize.value==='full'?1:Math.min(1,approvalWindow.clientWidth/width);
  approvalStage.style.transform='scale('+scale+')';approvalFit.style.width=Math.ceil(width*scale)+'px';approvalFit.style.height=Math.ceil(height*scale)+'px';
  approvalLabels.style.width=Math.ceil(width*scale)+'px';approvalLabels.style.gap=(16*scale)+'px';
  approvalWindow.style.maxHeight=approvalSize.value==='full'?'65vh':'none';
};
const approvalButtons=()=>{approveConvergence.disabled=approvalBusy||!approvalConfirmed||!handshakeConfirmed;rejectConvergence.disabled=approvalBusy;};
const failApproval=message=>{approvalFailed=true;approvalConfirmed=false;approvalStatus.textContent=message;clearTimeout(approvalTimeout);approvalButtons();};
const confirmApproval=async()=>{
  if(!approvalRendered||approvalFailed||approvalConfirmed||approvalConfirming||!handshakeConfirmed||approvalLoaded.size!==2||approvalObservations.size!==2)return;
  if(approvalReplay&&approvalReplayed.size!==2)return;
  const observations=Array.from(approvalObservations.values()),[a,b]=observations;
  if(a.route!==b.route||a.screen_id!==b.screen_id||(requiresSemanticScreen&&(!a.route||!a.screen_id)))return;
  if(approvalRestore?.route&&a.route!==approvalRestore.route)return;
  const generation=approvalGeneration;approvalConfirming=true;
  try{
    const response=await fetch(convergenceApproveEndpoint.replace('/approve','/preview-ready'),{method:'POST',headers:{'Content-Type':'application/json','X-ViewSpec-Frame-Nonce':nonce},
      body:JSON.stringify({preview_id:approvalId,nonce:approvalRendered.nonce,target:approvalSurface.value,observations})});
    if(generation!==approvalGeneration)return;
    if(!response.ok){failApproval('The comparison is no longer current. Reject it and ask for a revised change.');return;}
    approvalConfirmed=true;clearTimeout(approvalTimeout);
    approvalStatus.textContent='Both versions are ready. Compare the result, then approve or reject.';approvalButtons();
  }catch{if(generation===approvalGeneration)failApproval('The comparison could not be checked. Reload Studio to try again.');}
  finally{if(generation===approvalGeneration)approvalConfirming=false;}
};
const loadApprovalFrames=()=>{
  const surface=approvalSurface.value,paths=approvalRendered?.surfaces?.[surface];if(!paths)return;
  approvalGeneration++;approvalConfirmed=false;approvalConfirming=false;approvalFailed=false;
  approvalObservations.clear();approvalLoaded.clear();approvalReplayed.clear();approvalStage.replaceChildren();
  approvalStatus.textContent='Loading both versions…';approvalButtons();
  approvalFrames=['current','proposed'].map(side=>{
    const item=document.createElement('iframe');item.id='approval-'+side;item.dataset.approvalSide=side;
    item.title=(side==='current'?'Current':'Proposed')+' interface';item.setAttribute('sandbox','allow-scripts allow-forms');
    item.addEventListener('load',fitApproval);approvalStage.append(item);item.src=paths[side];return item;
  });
  clearTimeout(approvalTimeout);approvalTimeout=setTimeout(()=>failApproval('Both interfaces must finish rendering before you can approve. Reload Studio to try again.'),15000);
  fitApproval();
};
const renderApproval=value=>{
  if(!value){
    const wasActive=approvalId!==null;approvalGeneration++;approvalId=null;approvalKey=null;approvalRendered=null;approvalConfirmed=false;
    approvalFrames=[];approvalStage.replaceChildren();clearTimeout(approvalTimeout);normalStage.hidden=false;normalStage.removeAttribute('inert');mode.disabled=false;
    if(wasActive)requestAnimationFrame(fitCanvas);return;
  }
  const id=value.pending_preview.preview_id,newProposal=id!==approvalId;
  normalStage.hidden=true;normalStage.setAttribute('inert','');mode.disabled=true;
  if(newProposal){
    approvalGeneration++;approvalId=id;approvalKey=null;approvalRendered=null;approvalConfirmed=false;approvalConfirming=false;approvalFailed=false;
    approvalFrames=[];approvalStage.replaceChildren();approvalObservations.clear();approvalLoaded.clear();approvalReplayed.clear();clearTimeout(approvalTimeout);
    approvalRestore=retainedContext?.route?{...retainedContext}:null;
    const choice=Array.from(replayChoices.values()).find(item=>item.checkpoint.evidence_ref===activeReplayRef);
    approvalCheckpoint=choice?{evidence_ref:choice.checkpoint.evidence_ref,events:choice.replay.checkpoints.slice(1,choice.checkpoint.index+1).map(item=>item.event)}:null;
    approvalReplay=approvalCheckpoint;approvalState.value=choice?'checkpoint':'initial';document.getElementById('approval-state-label').hidden=!choice;
    approvalContext.textContent=choice?'Checkpoint: '+choice.checkpoint.label:'Initial state · screen and scroll stay in sync';
    annotate=false;mode.textContent='Comment';frames.forEach(item=>item.contentWindow.postMessage({type:'viewspec-review-mode',nonce,annotate:false},'*'));
    closePanel(false);document.getElementById('approval-title').focus({preventScroll:true});resetCanvas();status.textContent='Compare current and proposed interfaces';
  }
  approvalButtons();const rendered=value.rendered_preview;
  if(rendered?.status!=='ready'){
    approvalConfirmed=false;approvalButtons();
    if(approvalKey){approvalGeneration++;approvalKey=null;approvalRendered=null;approvalFrames=[];approvalStage.replaceChildren();clearTimeout(approvalTimeout);}
    approvalStatus.textContent=rendered?.status==='failed'?rendered.message:'Preparing proposed preview…';return;
  }
  approvalRendered=rendered;const key=id+':'+rendered.nonce;
  if(key!==approvalKey){
    approvalKey=key;const targets=Object.keys(rendered.surfaces||{});approvalSurface.replaceChildren(...targets.map(target=>{
      const option=document.createElement('option');option.value=target;option.textContent=target==='react-tailwind-app'?'Live':target==='html-tailwind-app'?'Static':'Preview';return option;
    }));approvalSurface.value=targets.includes(surfaceSelect?.value)?surfaceSelect.value:targets[0];
    document.getElementById('approval-surface-label').hidden=targets.length<2;loadApprovalFrames();
  }else confirmApproval();
};
approvalSurface.addEventListener('change',loadApprovalFrames);approvalSize.addEventListener('change',fitApproval);
approvalState.addEventListener('change',()=>{
  approvalReplay=approvalState.value==='checkpoint'?approvalCheckpoint:null;
  approvalContext.textContent=approvalReplay?'Active checkpoint · screen and scroll stay in sync':'Initial state · screen and scroll stay in sync';
  loadApprovalFrames();
});
document.getElementById('viewport').addEventListener('change',()=>{fitApproval();if(approvalId)requestAnimationFrame(lockCanvasOrigin);});
if('ResizeObserver'in window)new ResizeObserver(fitApproval).observe(approvalWindow);
addEventListener('message',e=>{
  const item=approvalFrames.find(frame=>frame.contentWindow===e.source),data=e.data;
  if(!item||!data||!approvalRendered||data.nonce!==approvalRendered.nonce||data.surface_target!==approvalSurface.value)return;
  const side=item.dataset.approvalSide;
  if(data.type==='viewspec-review-render-failed'){failApproval('One of the interfaces could not render. Reject it and ask for a revised change.');return;}
  if(data.type==='viewspec-review-ready'){
    approvalLoaded.add(side);approvalObservations.set(side,{side,route:requiresSemanticScreen?data.route:null,screen_id:data.screen_id});
    if(approvalLoaded.size===2){
      if(approvalReplay)approvalFrames.forEach(frame=>frame.contentWindow.postMessage({type:'viewspec-studio-replay-apply',nonce:approvalRendered.nonce,...approvalReplay},'*'));
      else if(approvalRestore)approvalFrames.forEach(frame=>frame.contentWindow.postMessage({type:'viewspec-review-restore',nonce:approvalRendered.nonce,...approvalRestore},'*'));
    }
    confirmApproval();return;
  }
  if(data.type==='viewspec-studio-replay-result'){
    if(data.ok!==true){failApproval('This checkpoint could not be shown in both versions. Choose Initial state to compare the change.');return;}
    approvalReplayed.add(side);
    if(approvalReplayed.size===2&&approvalRestore)approvalFrames.forEach(frame=>frame.contentWindow.postMessage({type:'viewspec-review-restore',nonce:approvalRendered.nonce,...approvalRestore},'*'));
    confirmApproval();return;
  }
  if(data.type==='viewspec-review-context'&&approvalLoaded.has(side)){
    approvalObservations.set(side,{side,route:data.route,screen_id:data.screen_id});
    if(data.sync_cause==='navigation'){
      approvalRestore={route:data.route,scroll_x:0,scroll_y:0};approvalConfirmed=false;approvalButtons();
    }
    if(data.sync_cause==='navigation'||data.sync_cause==='passive'){
      const other=approvalFrames.find(frame=>frame!==item);
      other?.contentWindow.postMessage({type:'viewspec-review-restore',nonce:approvalRendered.nonce,route:data.route,scroll_x:data.scroll_x,scroll_y:data.scroll_y},'*');
    }
    confirmApproval();
  }
});
"""
