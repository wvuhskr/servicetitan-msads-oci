// Browser reference: remember the ad click for your website's booking server.
// This cookie is an untrusted hint, not proof of a customer's identity or click.
// Never embed CAPTURE_BEARER here or forward arbitrary browser events to /c.
// See docs/capture.md for the required verified server integration.
(function () {
  var KEY = "st_msads_oci_msclkid";
  var q = new URLSearchParams(location.search).get("msclkid");
  if (q && /^[A-Za-z0-9_-]{4,128}$/.test(q)) {
    document.cookie = KEY + "=" + q + ";max-age=" + 90 * 86400 + ";path=/;SameSite=Lax;Secure";
  }
})();
