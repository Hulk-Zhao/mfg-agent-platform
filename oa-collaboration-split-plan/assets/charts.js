// assets/charts.js — ECharts 图表逻辑（方案报告）
(function () {
  "use strict";

  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue("--accent").trim() || "#2b5cff";
  var accent2 = style.getPropertyValue("--accent2").trim() || "#ef8a1f";
  var ink = style.getPropertyValue("--ink").trim() || "#121a2a";
  var muted = style.getPropertyValue("--muted").trim() || "#5a6679";
  var rule = style.getPropertyValue("--rule").trim() || "#e4e9f2";

  // ---------- Chart: 竞品能力对标雷达 ----------
  var radarEl = document.getElementById("chart-radar");
  if (radarEl) {
    var chart = echarts.init(radarEl, null, { renderer: "svg", animation: false });
    chart.setOption({
      color: [accent, accent2, muted, accent + "99", accent2 + "99"],
      tooltip: { appendToBody: true, trigger: "item" },
      legend: {
        bottom: 0,
        textStyle: { color: ink, fontSize: 12 },
        itemWidth: 14, itemHeight: 8
      },
      radar: {
        center: ["50%", "46%"],
        radius: "62%",
        indicator: [
          { name: "双入口形态", max: 5 },
          { name: "报销自动化", max: 5 },
          { name: "差旅一体化", max: 5 },
          { name: "智能审批", max: 5 },
          { name: "留痕 / HITL 治理", max: 5 }
        ],
        axisName: { color: muted, fontSize: 12 },
        splitLine: { lineStyle: { color: rule } },
        axisLine: { lineStyle: { color: rule } },
        splitArea: { areaStyle: { color: ["#ffffff", "#f7f9fd"] } }
      },
      series: [{
        type: "radar",
        symbolSize: 5,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.06 },
        label: { show: false },
        data: [
          { value: [3.0, 0.0, 0.0, 2.0, 4.0], name: "本平台（现状）" },
          { value: [4.5, 3.5, 3.0, 4.0, 4.5], name: "本平台（目标）" },
          { value: [5.0, 4.0, 5.0, 5.0, 4.0], name: "钉钉" },
          { value: [4.5, 4.0, 4.0, 4.0, 4.5], name: "飞书" },
          { value: [4.0, 5.0, 4.5, 4.0, 3.5], name: "合思（费控）" }
        ]
      }]
    });
    window.addEventListener("resize", function () { chart.resize(); });
  }
})();
