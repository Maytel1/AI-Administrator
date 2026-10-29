/* Доступность модальных окон.
 *
 * - Клавиша Esc закрывает все открытые модалки (снимает checked с .modal-toggle).
 * - При открытии модалки фокус переносится на первый фокусируемый элемент
 *   внутри (кнопка/поле/ссылка), иначе — на саму модалку.
 *
 * Отдельный файл, а не инлайн: CSP панели — script-src 'self' без 'unsafe-inline'.
 */
(function () {
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-toggle:checked').forEach(function (t) {
        t.checked = false;
      });
    }
  });

  document.addEventListener('click', function (e) {
    var label = e.target.closest('label[for]');
    if (!label) return;
    var id = label.getAttribute('for');
    if (!id) return;
    var toggle = document.getElementById(id);
    if (!toggle || !toggle.classList.contains('modal-toggle')) return;

    setTimeout(function () {
      if (toggle.checked) {
        var modal = document.querySelector('.modal-toggle:checked ~ .modal');
        if (modal) {
          modal.setAttribute('aria-modal', 'true');
          var focusable = modal.querySelector(
            'button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
          );
          if (focusable) {
            focusable.focus();
          } else {
            modal.focus();
          }
        }
      }
    }, 50);
  });
})();