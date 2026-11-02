(() => {
  const el = document.querySelector('g.node[data-id="song"]');
  if (!el) return 'song node not found';
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  return 'clicked song';
})()
