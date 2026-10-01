// Дежурный24 — интерактив прототипа
document.addEventListener('DOMContentLoaded', () => {
  // --- калькулятор ущерба ---
  const calls = document.getElementById('calls');
  const check = document.getElementById('check');
  const missed = document.getElementById('missed');
  if (calls) {
    const fmt = n => Math.round(n).toLocaleString('ru-RU');
    const recalc = () => {
      document.getElementById('callsV').textContent = calls.value;
      document.getElementById('checkV').textContent = fmt(check.value) + ' ₽';
      document.getElementById('missedV').textContent = missed.value + ' %';
      const loss = calls.value * 30 * (missed.value / 100) * check.value;
      document.getElementById('lossV').textContent = fmt(loss) + ' ₽/мес';
      document.getElementById('retV').textContent =
        'Дежурный24 вернёт около ' + fmt(loss * 0.8) + ' ₽/мес (спасём ~80% звонков)';
    };
    [calls, check, missed].forEach(el => el.addEventListener('input', recalc));
    recalc();
  }
  // --- табы вертикалей ---
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
      document.querySelectorAll('.pane').forEach(p => p.classList.remove('on'));
      tab.classList.add('on');
      document.getElementById(tab.dataset.pane).classList.add('on');
    });
  });
});
