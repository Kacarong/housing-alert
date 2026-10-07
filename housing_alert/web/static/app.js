(function () {
  "use strict";
  var base = window.HA_BASE || "";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderPreview(el, data) {
    var html = "<p><b>" + data.count + "</b>건 매칭 (저장된 공고 " + data.total_notices + "건 중";
    if (data.coop_blocked) html += ", 협동조합 의심으로 빠진 " + data.coop_blocked + "건 별도";
    html += ")</p>";
    if (data.samples && data.samples.length) {
      html += "<ol>";
      data.samples.forEach(function (n) {
        html += '<li><a href="' + base + "/notices/" + n.id + '">' + esc(n.title) + "</a> <span class=\"muted small\">" +
          esc([n.org, n.supply_type, (n.sido || "").replace(/,/g, "·"), n.apply_end ? "~" + n.apply_end : ""].filter(Boolean).join(" · ")) +
          "</span></li>";
      });
      html += "</ol>";
      if (data.count > data.samples.length) html += '<p class="muted small">상위 ' + data.samples.length + "건만 표시</p>";
    }
    el.innerHTML = html;
  }

  var btn = document.getElementById("preview-btn");
  if (btn) {
    btn.addEventListener("click", function () {
      var form = document.getElementById("filter-form");
      var out = document.getElementById("preview-result");
      out.textContent = "계산 중…";
      fetch(base + "/api/filters/preview", { method: "POST", body: new FormData(form), credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (d) { renderPreview(out, d); out.scrollIntoView({ behavior: "smooth" }); })
        .catch(function (e) { out.textContent = "미리보기 실패: " + e.message; });
    });
  }

  document.querySelectorAll(".js-preview").forEach(function (b) {
    b.addEventListener("click", function () {
      var out = document.getElementById("preview-" + b.dataset.id);
      if (out.innerHTML) { out.innerHTML = ""; return; }
      out.textContent = "불러오는 중…";
      fetch(base + "/api/filters/" + b.dataset.id + "/preview", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) { renderPreview(out, d); })
        .catch(function (e) { out.textContent = "실패: " + e.message; });
    });
  });
})();
