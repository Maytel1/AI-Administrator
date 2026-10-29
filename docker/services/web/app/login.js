/* Галочка «Запомнить меня»: браузер предлагает сохранить логин и пароль
   только если её поставили. Меняем autocomplete поля пароля на submit —
   надёжнее, чем полагаться на дефолтное предложение браузера.
   Без JavaScript галочка всё равно работает: сервер ставит долгую cookie.

   При любой отправке формы паролю ставим current-password, чтобы браузер
   предложил сохранить введённые логин+пароль после успешного входа. */
(function () {
  var form = document.querySelector(".login form");
  if (!form) return;
  var pwd = document.getElementById("pwd");
  var box = document.getElementById("remember");
  form.addEventListener("submit", function () {
    if (pwd) pwd.autocomplete = "current-password";
    if (box && box.checked) {
      // «Запомнить меня» — долгая cookie с логином для предзаполнения.
      pwd.autocomplete = "current-password";
    }
  });
})();
