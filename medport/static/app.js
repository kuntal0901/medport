// MedPort: small progressive enhancements. Every page still works without this file.
(function () {
  "use strict";
  var root = document.documentElement;

  // ---- light / dark toggle (follows the system until the user picks)
  var toggle = document.getElementById("theme-toggle");
  if (toggle) toggle.addEventListener("click", function () {
    var dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("medport-theme", root.dataset.theme); } catch (e) {}
  });

  // ---- toasts
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".toast button");
    if (b) b.closest(".toast").remove();
  });
  if (location.search.match(/[?&](ok|error)=/)) {  // don't re-show the toast on refresh
    var u = new URL(location.href); u.searchParams.delete("ok"); u.searchParams.delete("error");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
  }

  // ---- drag-and-drop upload
  document.querySelectorAll("form.drop").forEach(function (form) {
    var input = form.querySelector("input[type=file]");
    var chosen = form.querySelector(".chosen");
    function show() {
      var n = input.files.length;
      if (!chosen) return;
      chosen.textContent = n === 0 ? "" : n === 1 ? input.files[0].name : n + " files selected";
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (t) {
      form.addEventListener(t, function (e) { e.preventDefault(); form.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      form.addEventListener(t, function (e) { e.preventDefault(); if (t === "drop" || !form.contains(e.relatedTarget)) form.classList.remove("over"); });
    });
    form.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      show();
      form.requestSubmit();
    });
  });
  // let people drop a file anywhere on the page, not only on the box
  var mainDrop = document.querySelector("form.drop");
  if (mainDrop) {
    window.addEventListener("dragover", function (e) { e.preventDefault(); mainDrop.classList.add("over"); });
    window.addEventListener("drop", function (e) {
      if (mainDrop.contains(e.target)) return;
      e.preventDefault(); mainDrop.dispatchEvent(new DragEvent("drop", { dataTransfer: e.dataTransfer }));
    });
  }

  // ---- busy state on buttons for slow posts (uploads, reading, backups)
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) { e.preventDefault(); return; }
    var btn = e.submitter || form.querySelector("button");
    if (!btn || !form.hasAttribute("data-busy")) return;
    setTimeout(function () {  // after the browser has read the button's name/value
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>' + form.dataset.busy;
    }, 0);
  });

  // ---- search the lab results
  var filter = document.querySelector("[data-filter]");
  if (filter) filter.addEventListener("input", function () {
    var q = filter.value.trim().toLowerCase(), any = false;
    document.querySelectorAll("[data-filterable]").forEach(function (block) {
      var shown = 0;
      block.querySelectorAll("tbody tr").forEach(function (tr) {
        var hit = !q || tr.textContent.toLowerCase().indexOf(q) !== -1;
        tr.hidden = !hit; if (hit) shown++;
      });
      block.hidden = shown === 0; any = any || shown > 0;
    });
    var none = document.querySelector(".no-match");
    if (none) none.style.display = any ? "none" : "block";
  });

  // ---- review: highlight rows with unsaved edits
  document.querySelectorAll("table.edit input").forEach(function (inp) {
    inp.addEventListener("input", function () {
      var tr = inp.closest("tr");
      if (tr && !tr.classList.contains("add")) tr.classList.add("dirty");
    });
  });
  var confirmBtn = document.querySelector("[data-confirm-all]");
  if (confirmBtn) confirmBtn.closest("form").addEventListener("submit", function (e) {
    var dirty = document.querySelectorAll("tr.dirty").length;
    if (dirty && !confirm(dirty + " row(s) have unsaved changes. Confirm anyway and lose them?")) e.preventDefault();
  });

  // ---- live progress while a report is being read
  var reading = document.querySelector("[data-progress]");
  if (reading) {
    var t0 = Date.now(), step = reading.querySelector("[data-step]"), clock = reading.querySelector("[data-elapsed]");
    setInterval(function () {
      fetch(reading.dataset.progress).then(function (r) { return r.json(); }).then(function (p) {
        if (!p.reading) { location.reload(); return; }
        if (p.step) step.textContent = p.step + "\u2026";
        clock.textContent = Math.round((Date.now() - t0) / 1000) + "s so far";
      }).catch(function () {});
    }, 2000);
  }

  // ---- copy buttons and the share-link countdown
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var text = b.dataset.copy, label = b.innerHTML;
    function done() {
      b.classList.add("done"); b.textContent = "Copied";
      setTimeout(function () { b.classList.remove("done"); b.innerHTML = label; }, 1800);
    }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
    function fallback() {
      var inp = b.parentNode.querySelector("input");
      if (inp) { inp.focus(); inp.select(); try { document.execCommand("copy"); done(); } catch (err) {} }
    }
  });
  document.querySelectorAll("[data-expires]").forEach(function (el) {
    var end = +el.dataset.expires * 1000;
    (function tick() {
      var s = Math.max(0, Math.round((end - Date.now()) / 1000));
      if (!s) { el.textContent = "expired"; return; }
      var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
      el.textContent = "expires in " + (h ? h + "h " + m + "m" : m + "m " + (sec < 10 ? "0" : "") + sec + "s");
      setTimeout(tick, 1000);
    })();
  });

  // ---- phone pairing: say when the phone has paired, or when the code on screen stopped working
  var pairEl = document.querySelector("[data-pair-status]");
  if (pairEl) (function poll() {
    fetch("/share/pair/" + pairEl.dataset.pairStatus).then(function (r) { return r.json(); }).then(function (s) {
      if (s.state === "paired") { location.href = "/share?ok=" + encodeURIComponent("Paired with " + s.name) + "#phone"; return; }
      if (s.state === "expired") {
        var card = pairEl.closest(".qr-share");
        card.innerHTML = '<p class="muted">This code has expired.</p><form method="post" action="/share/pair"><button>Make a new code</button></form>';
        return;
      }
      setTimeout(poll, 2000);
    }).catch(function () { setTimeout(poll, 4000); });
  })();

  // ---- count up the summary numbers
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.querySelectorAll("[data-count]").forEach(function (el) {
      var end = +el.dataset.count, start = performance.now();
      if (!end) return;
      setTimeout(function () { el.textContent = end; }, 800);  // rAF pauses in background tabs
      (function tick(now) {
        var k = Math.min(1, (now - start) / 600);
        el.textContent = Math.round(end * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(tick);
      })(start);
    });
  }
})();
