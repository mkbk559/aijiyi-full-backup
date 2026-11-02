(() => {
  const el = document.querySelector('g.node[data-id="t_mcp"]');
  if (!el) return 'NOT FOUND';
  const before = location.hash;
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  const h2 = document.querySelector('#drawer h2');
  return JSON.stringify({
    before,
    after: location.hash,
    drawerOpen: document.getElementById('drawer').classList.contains('open'),
    h2: h2 ? h2.textContent : null
  });
})()
