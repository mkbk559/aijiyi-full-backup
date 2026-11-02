(() => {
  const ps = [...document.querySelectorAll('#drawer p')];
  const h = ps.find(x => x.textContent.includes('明确不采用'));
  if (!h) return 'not found';
  h.scrollIntoView({ block: 'start' });
  return 'scrolled to ' + h.textContent;
})()
