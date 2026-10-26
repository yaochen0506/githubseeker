// 只做兩件事：超過時限沒更新就顯示停更提示；預覽圖載入失敗就隱藏。
(function () {
  var body = document.body;
  var generated = Date.parse(body.dataset.generatedAt || "");
  var limitHours = Number(body.dataset.staleHours || 48);
  if (!isNaN(generated) && Date.now() - generated > limitHours * 3600 * 1000) {
    document.getElementById("stale-banner").hidden = false;
  }
  document.querySelectorAll("img.og").forEach(function (img) {
    img.addEventListener("error", function () { img.remove(); });
  });
})();
