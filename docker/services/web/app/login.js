/* Галочка «Запомнить меня»: браузер предлагает сохранить логин и пароль
   только если её поставили. Меняем autocomplete поля пароля на submit —
   надёжнее, чем полагаться на дефолтное предложение браузера.
   Без JavaScript галочка всё равно работает: сервер ставит долгую cookie. */
(function () {
  var form = document.querySelector(".login form");
  if (!form) return;
  var pwd = document.getElementById("pwd");
  var box = document.getElementById("remember");
  form.addEventListener("submit", function () {
    if (pwd) pwd.autocomplete = box && box.checked ? "current-password" : "off";
  });
})();
