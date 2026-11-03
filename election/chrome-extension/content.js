// Reads the live blog's posts and sends the newest to the barn, every
// minute. The page is reloaded every few minutes too, so nothing depends on
// NYT's own live-update script having inserted new posts.
const SEND_EVERY_MS = 60 * 1000;
const RELOAD_EVERY_MS = 4 * 60 * 1000;

function readPosts() {
  const posts = [];
  for (const c of document.querySelectorAll('[data-testid="reporter-update"]')) {
    const t = c.querySelector("time[datetime]");
    if (!t) continue;
    const byline = c.querySelector('[data-testid="live-blog-byline"]');
    // The name is the "last-byline" link; an empty link comes before it.
    const author = byline && (byline.querySelector("a.last-byline, [class*=\"last-byline\"]") || byline.querySelector("p"));
    const tagline = byline && byline.querySelector('[data-testid="lb-byline-tagline"]');
    const heading = c.querySelector("h2, h3");
    const paragraphs = [...c.querySelectorAll("p")]
      .filter(p => !(byline && byline.contains(p)))
      .map(p => p.innerText.trim())
      .filter(Boolean);
    const img = c.querySelector("img");
    posts.push({
      time: t.getAttribute("datetime"),
      author: author ? author.innerText.trim() : "",
      tagline: tagline ? tagline.innerText.trim() : "",
      headline: heading ? heading.innerText.trim() : "",
      paragraphs,
      image: img ? (img.currentSrc || img.src) : null,
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

send();
setInterval(send, SEND_EVERY_MS);
setTimeout(() => location.reload(), RELOAD_EVERY_MS);
