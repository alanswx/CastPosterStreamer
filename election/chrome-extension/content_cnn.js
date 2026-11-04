// CNN's live updates, sent the same way as the NYT blog (see content.js).
const SEND_EVERY_MS = 60 * 1000;
const RELOAD_EVERY_MS = 4 * 60 * 1000;

function readPosts() {
  const posts = [];
  for (const c of document.querySelectorAll("article.live-story-post")) {
    // Most of the page is empty placeholders until scrolled to.
    if (!c.querySelector("time.live-story-post__timestamp")) continue;
    const when = new Date(c.getAttribute("data-last-updated"));
    if (isNaN(when)) continue;
    const heading = c.querySelector("h2.live-story-post__headline");
    const byline = c.querySelector("span.live-story-post__byline");
    // Collapsed posts hide their text, so textContent rather than innerText.
    const paragraphs = [...c.querySelectorAll("section.live-story-post__body p")]
      .map(p => p.textContent.replace(/\s+/g, " ").trim())
      .filter(Boolean);
    posts.push({
      time: when.toISOString(),
      author: byline ? byline.textContent.trim().replace(/^By\s+/i, "").replace(/\s+in\s+.*$/, "") : "",
      headline: heading ? heading.textContent.trim() : "",
      paragraphs,
    });
  }
  posts.sort((a, b) => b.time.localeCompare(a.time));
  return posts.slice(0, 12);
}

function send() {
  const posts = readPosts();
  if (!posts.length) return;
  chrome.runtime.sendMessage(
    { type: "posts", payload: { source: location.href, fetched: new Date().toISOString(), posts } },
    r => console.log("[barn feed]", r)
  );
}

// Give the page a moment to fill in its newest posts.
setTimeout(send, 5000);
setInterval(send, SEND_EVERY_MS);
setTimeout(() => location.reload(), RELOAD_EVERY_MS);
