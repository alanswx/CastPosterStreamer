// The page's own security policy forbids it from contacting this Mac, so
// the content script hands posts to this worker, which isn't bound by it.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== "posts") return;
  fetch("http://localhost:5055/posts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.payload),
  })
    .then(r => r.json()).then(j => reply({ ok: true, ...j }))
    .catch(e => reply({ ok: false, error: String(e) }));
  return true; // reply asynchronously
});
