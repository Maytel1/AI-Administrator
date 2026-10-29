/* Переключатель точки: выбор в списке сразу уводит на /salon (сервер ставит
   cookie салона и редиректит обратно на эту же страницу). Внешний файл, а не
   инлайновый onchange — CSP панели запрещает inline-скрипты, из-за чего раньше
   выбор точки ничего не делал. Без JavaScript форма всё равно отправляется
   по Enter (кнопки нет, но change на <select> дёргает form.submit()). */
(function () {
  var sel = document.getElementById("salon-select");
  if (sel && sel.form) {
    sel.addEventListener("change", function () {
      sel.form.submit();
    });
  }
})();