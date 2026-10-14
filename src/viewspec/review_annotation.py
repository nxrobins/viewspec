"""Temporary keyboard focus model for semantic annotation in checked frames."""

FRAME_ANNOTATION_JS = r"""
const annotationTabs=new Map();let annotationTarget=null,annotationStatus=null;
const validAnnotationTarget=element=>element instanceof HTMLElement&&element.matches('[id][data-ir-id]')&&
  element.id.length<=256&&element.dataset.irId&&element.dataset.irId.length<=256&&
  !element.closest('[hidden],[inert],[aria-hidden="true"]')&&!element.matches(':disabled')&&
  element.getClientRects().length>0&&element.getBoundingClientRect().width>0&&element.getBoundingClientRect().height>0&&
  getComputedStyle(element).visibility==='visible'&&(!element.checkVisibility||element.checkVisibility({opacityProperty:true,visibilityProperty:true}));
const annotationTargets=()=>Array.from(document.querySelectorAll('[id][data-ir-id]')).filter(validAnnotationTarget);
const visibleSemanticElement=id=>Array.from(document.querySelectorAll('[id][data-ir-id]')).find(element=>element.id===id&&validAnnotationTarget(element));
const restoreAnnotationTab=element=>{const original=annotationTabs.get(element);
  if(original===null)element.removeAttribute('tabindex');else element.setAttribute('tabindex',original);
  element.removeAttribute('data-viewspec-review-target');annotationTabs.delete(element);};
const reconcileAnnotation=()=>{if(!annotate)return[];const list=annotationTargets(),previous=annotationTarget;
  for(const element of annotationTabs.keys())if(!list.includes(element))restoreAnnotationTab(element);
  annotationTarget=list.includes(previous)?previous:(list.includes(document.activeElement)?document.activeElement:list[0]||null);
  for(const element of list){if(!annotationTabs.has(element))annotationTabs.set(element,element.getAttribute('tabindex'));
    element.setAttribute('tabindex',element===annotationTarget?'0':'-1');element.setAttribute('data-viewspec-review-target','true');}
  if(previous&&!list.includes(previous)&&document.hasFocus())annotationTarget?.focus();return list;};
const announceAnnotation=element=>{if(!annotationStatus)return;const list=annotationTargets();
  const label=String(element.getAttribute('aria-label')||element.textContent||element.id).replace(/\s+/g,' ').trim().slice(0,96);
  annotationStatus.textContent=label+'. Target '+(list.indexOf(element)+1)+' of '+list.length+'. Enter or Space to comment.';};
const focusAnnotation=element=>{if(!validAnnotationTarget(element))return;annotationTarget=element;reconcileAnnotation();
  element.focus({preventScroll:true});const box=element.getBoundingClientRect();
  if(box.bottom<=0||box.top>=innerHeight||box.right<=0||box.left>=innerWidth)element.scrollIntoView({block:'nearest',inline:'nearest'});
  announceAnnotation(element);};
const annotationObserver=new MutationObserver(()=>reconcileAnnotation());
const setAnnotationMode=value=>{annotate=value;document.documentElement.dataset.viewspecReviewMode=annotate?'annotate':'explore';
  if(annotate){if(!annotationStatus){annotationStatus=document.createElement('div');annotationStatus.className='viewspec-annotation-status';
    annotationStatus.setAttribute('role','status');annotationStatus.setAttribute('aria-live','polite');document.body.append(annotationStatus);}
    reconcileAnnotation();annotationObserver.observe(document.body,{subtree:true,childList:true,attributes:true,
      attributeFilter:['hidden','inert','aria-hidden','disabled','class','style','id','data-ir-id']});}
  else{annotationObserver.disconnect();for(const element of annotationTabs.keys())restoreAnnotationTab(element);
    annotationTarget=null;annotationStatus?.remove();annotationStatus=null;setSelected(null);}};
addEventListener('resize',()=>reconcileAnnotation());
addEventListener('focusin',event=>{if(annotate&&validAnnotationTarget(event.target)){annotationTarget=event.target;
  reconcileAnnotation();announceAnnotation(event.target);}});
addEventListener('keydown',event=>{if(!annotate||event.metaKey||event.ctrlKey||event.altKey)return;
  const list=reconcileAnnotation();if(!list.length)return;
  const index=list.indexOf(document.activeElement);
  let next=null;
  if(event.key==='ArrowDown')next=list[(index+1)%list.length];
  else if(event.key==='ArrowUp')next=list[index<0?list.length-1:(index-1+list.length)%list.length];
  else if(event.key==='Home')next=list[0];else if(event.key==='End')next=list[list.length-1];
  if(next){event.preventDefault();event.stopImmediatePropagation();focusAnnotation(next);}
  else if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopImmediatePropagation();
    if(validAnnotationTarget(document.activeElement))choose(document.activeElement);}
},true);
"""

FRAME_ANNOTATION_CSS = (
    b"html[data-viewspec-review-mode=annotate] [data-viewspec-review-target]{cursor:crosshair!important}"
    b"html[data-viewspec-review-mode=annotate] [data-viewspec-review-target]:focus{"
    b"outline:3px solid #1971d4!important;outline-offset:3px!important}"
    b".viewspec-annotation-status{position:absolute!important;width:1px!important;height:1px!important;"
    b"padding:0!important;margin:-1px!important;overflow:hidden!important;clip-path:inset(50%)!important;"
    b"white-space:nowrap!important;border:0!important}"
)
