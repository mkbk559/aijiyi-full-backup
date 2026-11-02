(() => {
  const hs = [...document.querySelectorAll('#drawer h3')];
  const h = hs.find(x => x.textContent.includes('DSH'));
  if (!h) return 'not found';
  h.scrollIntoView({ block: 'start' });
  return 'scrolled to ' + h.textContent;
})()
