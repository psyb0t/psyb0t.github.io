// Shared visitor tracking for psyb0t.github.io and every project docs site
// served under it. Each page loads this one file:
//   <script src="https://psyb0t.github.io/analytics.js" async></script>
// so changing the tracker here changes it everywhere, without rebuilding the
// sites.
(function () {
  if (window.sc_project) {
    return;
  }
  window.sc_project = 13365931;
  window.sc_invisible = 1;
  window.sc_security = "ea8ca039";
  var counter = document.createElement("script");
  counter.src = "https://www.statcounter.com/counter/counter.js";
  counter.async = true;
  document.head.appendChild(counter);
})();
