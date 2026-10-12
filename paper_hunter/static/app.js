const $ = (id) => document.getElementById(id);

let csvText = "";
let csvName = "";
let jobId = null;
let results = [];
let pollTimer = null;
let parseTimer = null;
let browserAvailable = true;
let lastState = "";
let sawActive = false; // this page watched the run while it was going (not a reload of a finished run)

const ACTIVE = ["running", "pausing", "paused", "stopping"];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

function input() {
  return {
    text: $("dois").value,
    csv: csvText,
    use_browser: $("use-browser").checked,
    parts: Math.max(1, Number($("parts").value) || 1),
  };
}

function showError(message) {
  $("input-error").textContent = message;
  $("input-error").hidden = !message;
}

// --- 1. DOIs ---------------------------------------------------------------

function scheduleParse() {
  clearTimeout(parseTimer);
  parseTimer = setTimeout(parse, 300);
}

async function parse() {
  showError("");
  if (!$("dois").value.trim() && !csvText) {
    $("parse-status").textContent = "Paste DOIs or choose a CSV file. The DOI column is found automatically.";
    $("start").disabled = true;
    return;
  }
  try {
    const { count, column } = await api("/api/parse", { method: "POST", body: JSON.stringify(input()) });
    let text = `${count} DOI${count === 1 ? "" : "s"} found`;
    if (column !== null) text += ` (column "${column}" in ${csvName})`;
    $("parse-status").textContent = count ? `${text}. Empty rows and duplicates are skipped.` : "No DOIs found yet.";
    $("start").disabled = count === 0;
  } catch (e) {
    $("parse-status").textContent = "";
    showError(e.message);
    $("start").disabled = true;
  }
}

$("dois").addEventListener("input", scheduleParse);

$("csv").addEventListener("change", async () => {
  const file = $("csv").files[0];
  if (!file) return;
  csvText = await file.text();
  csvName = file.name;
  $("csv-name").textContent = file.name;
  $("csv-clear").hidden = false;
  parse();
});

$("csv-clear").addEventListener("click", clearCsv);

function clearCsv() {
  csvText = "";
  csvName = "";
  $("csv").value = "";
  $("csv-name").textContent = "No file chosen";
  $("csv-clear").hidden = true;
  parse();
}

$("start").addEventListener("click", async () => {
  showError("");
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify(input()) });
    follow(job.id);
  } catch (e) {
    showError(e.message);
  }
});

function setInputEnabled(enabled) {
  for (const id of ["dois", "csv", "parts", "start"]) $(id).disabled = !enabled;
  $("use-browser").disabled = !enabled || !browserAvailable;
  if (enabled) parse();
}

// --- 2. Progress -----------------------------------------------------------

function follow(id) {
  jobId = id;
  results = [];
  $("results").replaceChildren();
  $("log").textContent = "";
  $("zip-list").replaceChildren();
  $("progress-section").hidden = false;
  $("download-section").hidden = true;
  $("controls").hidden = false;
  lastState = "";
  sawActive = false;
  setInputEnabled(false);
  poll();
}

async function poll() {
  clearTimeout(pollTimer);
  let job;
  try {
    job = await api(`/api/jobs/${jobId}?since=${results.length}`);
  } catch (e) {
    $("state").textContent = "Lost connection to Paper Hunter. Is it still running?";
    pollTimer = setTimeout(poll, 3000);
    return;
  }
  render(job);
  if (ACTIVE.includes(job.state)) {
    sawActive = true;
    pollTimer = setTimeout(poll, 1000);
  } else {
    finished(job);
  }
}

function render(job) {
  for (const r of job.results) addRow(r);
  $("c-done").textContent = job.done;
  $("c-total").textContent = job.total;
  $("c-ok").textContent = job.succeeded;
  $("c-failed").textContent = job.failed;
  $("bar").max = job.total || 1;
  $("bar").value = job.done;
  $("log").textContent = job.log.join("\n");
  $("state").textContent = {
    running: `Working on DOI ${Math.min(job.done + 1, job.total)} of ${job.total}…`,
    pausing: "Pausing after the current paper…",
    paused: `Paused after ${job.done} of ${job.total} DOIs. Press Resume to continue.`,
    stopping: "Stopping after the current paper, then making the ZIP…",
    done: "Finished.",
    stopped: `Stopped after ${job.done} of ${job.total} DOIs.`,
    error: `Stopped because of an error: ${job.error}`,
  }[job.state];

  lastState = job.state;
  const pausedOrPausing = job.state === "paused" || job.state === "pausing";
  $("pause").textContent = pausedOrPausing ? "Resume" : "Pause";
  $("pause").disabled = job.state === "stopping";
  $("stop").disabled = job.state === "stopping";
}

function addRow(r) {
  results.push(r);
  const tr = document.createElement("tr");
  const link = document.createElement("a");
  link.href = `https://doi.org/${r.doi}`;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = r.doi;
  const cells = [String(results.length), link, r.success ? "Found" : "Not found", r.source || ""];
  cells.forEach((content, i) => {
    const td = document.createElement("td");
    td.append(content);
    if (i === 2) td.className = r.success ? "ok" : "bad";
    tr.append(td);
  });
  $("results").append(tr);
  const wrap = $("results").closest(".table-wrap");
  wrap.scrollTop = wrap.scrollHeight;
}

async function control(action) {
  $("pause").disabled = true;
  $("stop").disabled = true;
  try {
    render(await api(`/api/jobs/${jobId}/${action}`, { method: "POST" }));
  } catch (e) {
    $("state").textContent = e.message;
  }
  if (ACTIVE.includes(lastState)) {
    $("pause").disabled = lastState === "stopping";
    $("stop").disabled = lastState === "stopping";
  }
}

$("pause").addEventListener("click", () => {
  control(lastState === "paused" || lastState === "pausing" ? "resume" : "pause");
});

$("stop").addEventListener("click", () => control("stop"));

function finished(job) {
  $("controls").hidden = true;
  setInputEnabled(true);
  if (!job.done) return;
  $("download-section").hidden = false;
  $("folder").textContent = job.folder;
  $("summary-link").href = `/api/jobs/${jobId}/summary.csv`;
  $("resplit").value = Math.min(job.parts, job.done);
  $("resplit").max = job.done;
  $("retry").hidden = job.failed === 0;
  showZips(job.zips);
  // The run just ended while this page watched it: start the download right away.
  // (Several ZIPs are left as links, since browsers block multiple automatic downloads.)
  if (sawActive && job.zips.length === 1) {
    sawActive = false;
    const a = document.createElement("a");
    a.href = job.zips[0].url;
    a.download = job.zips[0].name;
    document.body.append(a);
    a.click();
    a.remove();
  }
}

// --- 3. Download -----------------------------------------------------------

function showZips(files) {
  $("zip-list").replaceChildren();
  for (const f of files) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = f.url;
    a.textContent = f.name;
    li.append(a, ` (${(f.size / 1048576).toFixed(1)} MB)`);
    $("zip-list").append(li);
  }
}

$("make-zips").addEventListener("click", async () => {
  const parts = Math.max(1, Number($("resplit").value) || 1);
  $("make-zips").disabled = true;
  try {
    showZips(await api(`/api/jobs/${jobId}/zips?parts=${parts}`));
  } catch (e) {
    const li = document.createElement("li");
    li.className = "error";
    li.textContent = e.message;
    $("zip-list").replaceChildren(li);
  }
  $("make-zips").disabled = false;
});

$("retry").addEventListener("click", () => {
  $("dois").value = results.filter((r) => !r.success).map((r) => r.doi).join("\n");
  resetSections();
  if (csvText) clearCsv();
  else parse();
});

$("new-run").addEventListener("click", () => {
  $("dois").value = "";
  resetSections();
  clearCsv();
});

function resetSections() {
  $("progress-section").hidden = true;
  $("download-section").hidden = true;
  window.scrollTo({ top: 0 });
  $("dois").focus();
}

// --- Start-up --------------------------------------------------------------

(async () => {
  try {
    const info = await api("/api/info");
    browserAvailable = info.browser_available;
    if (!browserAvailable) {
      $("use-browser").checked = false;
      $("use-browser").disabled = true;
      $("browser-note").hidden = false;
    }
    if (info.current_job) follow(info.current_job);
  } catch (e) {
    showError("Could not reach Paper Hunter. Is it still running?");
  }
})();
