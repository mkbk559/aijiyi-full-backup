(() => {
  const el = document.querySelector('g.node[data-id="t_mcp"]');
  if (!el) return 't_mcp node not found';
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  return 'clicked t_mcp';
})
