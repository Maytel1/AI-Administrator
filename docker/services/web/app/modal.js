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
        var modal = document.querySelector('.modal-toggle:checked + .modal');
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

  /* Модалка переписки диалога: загружает сообщения через fetch при клике на «Открыть». */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-dialog-id]');
    if (!btn) return;
    e.preventDefault();
    var dialogId = btn.getAttribute('data-dialog-id');
    var clientName = btn.getAttribute('data-client-name') || 'Клиент';
    var toggle = document.getElementById('dialog-modal-toggle');
    var title = document.getElementById('dialog-modal-title');
    var body = document.getElementById('dialog-modal-body');
    if (!toggle || !title || !body) return;
    title.textContent = 'Переписка — ' + clientName;
    body.innerHTML = '<div class="empty">Загрузка…</div>';
    toggle.checked = true;
    fetch('/dialogs/' + encodeURIComponent(dialogId) + '/messages')
      .then(function (r) { return r.text(); })
      .then(function (html) {
        body.innerHTML = html;
      })
      .catch(function () {
        body.innerHTML = '<div class="empty">Не удалось загрузить переписку</div>';
      });
  });
})();