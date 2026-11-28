// Host-owned glue in a CDP isolated world. Shared Lift code owns identity/actionability.
(() => {
  const lift = window.__viewspecLift;
  const signature = el => [el.tagName, el.getAttribute('type'), el.getAttribute('name'), el.getAttribute('form')];
  const text = el => el instanceof HTMLTextAreaElement ||
    (el instanceof HTMLInputElement && ['text', 'email', 'search', 'tel', 'url'].includes(el.type));
  const button = el => el instanceof HTMLButtonElement;
  const status = name => ({status: name});
  window.__viewspecRustDriver = {
    observe(selectors) {
      const targets = [];
      for (const selector of selectors) {
        let matches;
        try { matches = document.querySelectorAll(selector); } catch { return status('blocked'); }
        if (matches.length !== 1) return status('blocked');
        const el = matches[0];
        if (!text(el) && !button(el)) return status('unsupported');
        const ref = lift.refOf(el);
        if (targets.some(target => target.ref === ref)) return status('blocked');
        targets.push({ref, identity: lift.readTargets([ref])[ref].identity, signature: signature(el)});
      }
      return {status: 'ok', document: lift.docId, url: location.href, targets};
    },
    step({document: doc, url, target, operation, value}) {
      if (lift.docId !== doc || location.href !== url) return status('stale');
      const el = lift.get(target.ref);
      if (!el || !el.isConnected || el.ownerDocument !== document ||
          JSON.stringify(signature(el)) !== JSON.stringify(target.signature) ||
          lift.differsFrom(el, target.identity)) return status('changed');
      const isText = operation.endsWith('_text') || operation === 'check_focus';
      if (isText ? !text(el) : !button(el)) return status('unsupported');
      // Lift supplies cover/inert checks; the old bridge obtained these native
      // enabled/visible/editable checks from Playwright. The Rust host owns them now.
      if (el.matches(':disabled') || el.closest('[aria-disabled="true"]') ||
          !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) ||
          lift.blockedNow(el, isText, false) || (isText && el.readOnly)) return status('blocked');
      switch (operation) {
        case 'check_text': case 'check_button': return status('ok');
        case 'focus_text':
          el.focus(); el.select();
          return status(document.activeElement === el ? 'ok' : 'blocked');
        case 'check_focus': return status(document.activeElement === el ? 'ok' : 'blocked');
        case 'blur_text':
          if (document.activeElement !== el || el.value !== value) return status('changed');
          el.blur();
          return status('ok');
        case 'verify_text': return status(el.value === value ? 'ok' : 'changed');
        case 'scroll_button': el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); return status('ok');
        case 'point': {
          if (lift.blockedNow(el, false, true)) return status('blocked');
          const r = el.getBoundingClientRect();
          const x = (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2;
          const y = (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2;
          const hit = document.elementFromPoint(x, y);
          if (!hit || !el.contains(hit)) return status('blocked');
          return {status: 'ok', x, y};
        }
        default: return status('unsupported');
      }
    }
  };
})();
