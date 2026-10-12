"use strict";
const csrf = document.querySelector('meta[name="devportal-csrf"]').content;
const tbody = document.querySelector("#build-list");
const catalogMessage = document.querySelector("#catalog-message");
const downloadMessage = document.querySelector("#download-message");
const count = document.querySelector("#download-count");
const meter = document.querySelector("#download-progress");
let active = false;
let timer = null;
const bytes = size => `${new Intl.NumberFormat().format(size)} bytes`;

async function responseJson(url, options) {
  const response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Local request failed");
  return data;
}

async function catalog() {
  catalogMessage.textContent = "Loading catalog…";
  tbody.replaceChildren();
  try {
    const {builds} = await responseJson("/api/builds");
    catalogMessage.textContent = builds.length ? `${builds.length} build(s) available.` : "No builds are available for this key.";
    for (const build of builds) {
      const row = document.createElement("tr");
      for (const value of [build.filename, build.project_ref, build.platform, build.channel, bytes(build.size)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      const action = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Download / resume";
      button.disabled = active;
      button.addEventListener("click", () => start(build.build_id));
      action.append(button);
      row.append(action);
      tbody.append(row);
    }
  } catch (_) {
    catalogMessage.textContent = "Catalog unavailable; check the local service and DevHub connection.";
  }
}

async function start(buildId) {
  try {
    await responseJson("/api/download", {
      method: "POST", headers: {"Content-Type": "application/json", "X-DevPortal-CSRF": csrf},
      body: JSON.stringify({build_id: buildId})
    });
    active = true;
    for (const button of tbody.querySelectorAll("button")) button.disabled = true;
    await progress();
  } catch (_) {
    downloadMessage.textContent = "Download request refused or already running.";
  }
}

async function progress() {
  try {
    const data = await responseJson("/api/progress");
    active = data.state === "queued" || data.state === "downloading";
    const done = data.bytes_done || 0;
    const total = data.total_bytes || 0;
    meter.value = total ? Math.min(100, Math.floor(done * 100 / total)) : 0;
    count.textContent = total ? `${bytes(done)} / ${bytes(total)}` : "";
    if (data.state === "complete") downloadMessage.textContent = `${data.filename} verified and saved locally.`;
    else if (data.state === "failed") downloadMessage.textContent = data.error || "Download failed; retry to resume.";
    else if (data.state === "queued") downloadMessage.textContent = "Preparing fresh download authorization…";
    else if (data.state === "downloading") downloadMessage.textContent = `Downloading ${data.filename}…`;
    else downloadMessage.textContent = "No active download.";
    for (const button of tbody.querySelectorAll("button")) button.disabled = active;
    if (active && !timer) timer = setInterval(progress, 900);
    if (!active && timer) { clearInterval(timer); timer = null; }
  } catch (_) {
    downloadMessage.textContent = "Local progress unavailable.";
    if (timer) { clearInterval(timer); timer = null; }
  }
}

document.querySelector("#refresh").addEventListener("click", catalog);
catalog();
progress();
