// 停更提示、預覽圖載入失敗就隱藏、專案名錄的搜尋框。
(function () {
  var body = document.body;
  var generated = Date.parse(body.dataset.generatedAt || "");
  var limitHours = Number(body.dataset.staleHours || 48);
  if (!isNaN(generated) && Date.now() - generated > limitHours * 3600 * 1000) {
    document.getElementById("stale-banner").hidden = false;
  }
  var search = document.getElementById("dir-search");
  if (search) {
    search.addEventListener("input", function () {
      var words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      document.querySelectorAll(".dir-group").forEach(function (group) {
        var visible = 0;
        group.querySelectorAll("li[data-search]").forEach(function (li) {
          var hit = words.every(function (w) { return li.dataset.search.indexOf(w) !== -1; });
          li.hidden = !hit;
          if (hit) visible += 1;
        });
        group.hidden = visible === 0;
        shown += visible;
      });
      document.getElementById("dir-count").textContent = shown;
    });
  }
  document.querySelectorAll("img.og").forEach(function (img) {
    img.addEventListener("error", function () { img.remove(); });
  });
})();
