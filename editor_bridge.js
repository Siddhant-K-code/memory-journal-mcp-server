(() => {
  const root = document.getElementById("trip-explorer");
  const config = JSON.parse(
    document.getElementById("trip-editor-config").textContent,
  );
  const button = document.getElementById("tx-render-film");
  const status = document.getElementById("tx-render-status");
  const preview = document.getElementById("tx-render-preview");
  const music = document.getElementById("tx-film-music");
  const pace = document.getElementById("tx-film-pace");
  if (!root || !button) return;
  button.hidden = false;
  music.options[0].textContent = config.music_available
    ? `${config.music_title} (${config.bpm} BPM)`
    : "No soundtrack configured";
  music.options[0].disabled = !config.music_available;
  if (!config.music_available) music.value = "none";
  let plan = null;
  root.addEventListener("trip:plan-change", (event) => {
    plan = event.detail;
  });
  root.dispatchEvent(new Event("trip:request-plan"));
  if (
    config.seed_plan &&
    !Array.isArray(
      window.openai?.widgetState?.privateContent?.tripExplorer?.film,
    )
  ) {
    root.dispatchEvent(
      new CustomEvent("trip:set-plan", { detail: config.seed_plan }),
    );
  }
  function showComplete(current) {
    status.textContent = `Ready: ${current.total} clips · ${current.seconds.toFixed(1)} seconds · 1080 × 1920`;
    preview.replaceChildren();
    const video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.src = current.video_url;
    video.style.width = "100%";
    video.style.maxWidth = "420px";
    const link = document.createElement("a");
    link.href = current.video_url;
    link.download = "trip-film.mp4";
    link.textContent = "Download video";
    link.className = "btn";
    preview.append(video, link);
    if (current.credit) {
      const credit = document.createElement("p");
      credit.textContent =
        current.credit.credit +
        " · " +
        current.credit.license +
        " · edited excerpt";
      credit.className = "text-small";
      preview.append(credit);
    }
    root.dispatchEvent(
      new CustomEvent("trip:film-rendered", { detail: current }),
    );
    button.disabled = false;
  }
  if (config.latest) showComplete(config.latest);
  button.onclick = async () => {
    if (!plan?.uuids?.length) {
      status.textContent = "Add at least one clip to your Motion plan.";
      return;
    }
    button.disabled = true;
    status.textContent = "Starting your film…";
    try {
      const response = await fetch("/api/render", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Trip-Token": config.token,
        },
        body: JSON.stringify({ plan, music: music.value, pace: pace.value }),
      });
      const job = await response.json();
      if (!response.ok) throw Error(job.error || "Could not start rendering.");
      const poll = async () => {
        const result = await fetch(
          "/api/render/" + encodeURIComponent(job.id),
          { cache: "no-store" },
        );
        if (!result.ok) throw Error("Could not read render progress.");
        const current = await result.json();
        if (current.status === "failed") throw Error(current.error);
        status.textContent = `Rendering ${current.completed} / ${current.total} clips…`;
        if (current.status === "complete") {
          showComplete(current);
        } else setTimeout(() => poll().catch(failed), 1000);
      };
      const failed = (error) => {
        status.textContent = error.message;
        button.disabled = false;
      };
      await poll();
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
    }
  };
})();
