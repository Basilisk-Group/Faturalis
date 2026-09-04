document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("login-form");
  var status = document.getElementById("login-status");
  var submitBtn = document.getElementById("login-submit");

  form.addEventListener("submit", function (event) {
    event.preventDefault();

    submitBtn.disabled = true;
    submitBtn.textContent = "A entrar...";
    status.textContent = "";

    // This is a demo gate, not real authentication. qr-bench has no user
    // accounts of its own (see ../README.md). Any email/password is accepted
    // and the visitor is sent straight to the running app.
    setTimeout(function () {
      var appUrl = window.FATURALIS_CONFIG.appUrl;
      window.location.href = appUrl;
    }, 500);
  });
});
