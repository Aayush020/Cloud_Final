function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? 'light' : 'dark');
  localStorage.setItem('theme', isDark ? 'light' : 'dark');
  const btn = document.querySelector('.theme-toggle, .theme-btn');
  if (btn) btn.innerHTML = isDark ? '🌙 <span>Dark Mode</span>' : '☀️ <span>Light Mode</span>';
}

(function () {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  window.addEventListener('DOMContentLoaded', () => {
    const btn = document.querySelector('.theme-toggle, .theme-btn');
    if (btn && saved === 'dark') btn.innerHTML = btn.innerHTML.replace('🌙', '☀️').replace('Dark Mode', 'Light Mode');
  });
})();
