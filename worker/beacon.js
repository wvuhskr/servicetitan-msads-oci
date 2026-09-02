// Reference beacon. Paste into your site (or a tag manager) with WORKER set to your Worker URL.
// 1) On every page view carrying ?msclkid=..., remember it for 90 days and tell the Worker
//    which dynamic tracked phone number this visitor sees (if you use DNI).
// 2) On any form submit, send msclkid + the email/phone typed so the Worker can bind them.
(function () {
  var WORKER = "https://oci.yourdomain.com";
  var KEY = "st_msads_oci_msclkid";
  var q = new URLSearchParams(location.search).get("msclkid");
  if (q && /^[A-Za-z0-9_-]{4,128}$/.test(q)) {
    document.cookie = KEY + "=" + q + ";max-age=" + 90 * 86400 + ";path=/;SameSite=Lax;Secure";
  }
  var m = (document.cookie.match(new RegExp("(?:^|; )" + KEY + "=([^;]*)")) || [])[1] || null;
  function post(body) {
    body.ts = new Date().toISOString();
    try { navigator.sendBeacon(WORKER + "/c", new Blob([JSON.stringify(body)], { type: "text/plain" })); }
    catch (e) { fetch(WORKER + "/c", { method: "POST", body: JSON.stringify(body), keepalive: true }); }
  }
  // Set window.stMsadsDniNumber = "(555) 555-0100" from your DNI script if you have one.
  if (m) post({ kind: "session", msclkid: m, dni_number: window.stMsadsDniNumber || null });
  document.addEventListener("submit", function (e) {
    var f = e.target; if (!(f instanceof HTMLFormElement)) return;
    var email = null, phone = null;
    Array.prototype.forEach.call(f.elements, function (el) {
      if (el.type === "email" || /mail/i.test(el.name || "")) email = email || el.value;
      if (el.type === "tel" || /phone|tel/i.test(el.name || "")) phone = phone || el.value;
    });
    post({ kind: "form", msclkid: m, email: email, phone: phone });
  }, true);
})();
