document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".thumb-up, .thumb-down");
  if (!btn) return;
  const container = btn.closest(".thumbs");
  if (!container) return;

  const wasActive = btn.classList.contains("active");
  const baseScore = btn.classList.contains("thumb-up") ? 1 : -1;
  const score = wasActive ? 0 : baseScore;

  let res;
  try {
    res = await fetch("/api/feedback/thumbs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        report_date: container.dataset.reportDate,
        item_id: container.dataset.itemId,
        score: score,
      }),
    });
  } catch (err) {
    console.error("thumbs fetch failed", err);
    return;
  }

  if (!res.ok) {
    console.error("thumbs POST rejected:", res.status);
    return;
  }

  container.querySelectorAll("button").forEach(b => b.classList.remove("active"));
  if (score !== 0) btn.classList.add("active");
});
