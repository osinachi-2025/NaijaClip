document.querySelectorAll('.nc-password-toggle[data-password-target]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.passwordTarget);
    if (!input) return;
    const visible = input.type === 'text';
    const label = button.dataset.passwordLabel || 'password';
    input.type = visible ? 'password' : 'text';
    button.textContent = visible ? 'Show' : 'Hide';
    button.setAttribute('aria-label', `${visible ? 'Show' : 'Hide'} ${label}`);
    button.setAttribute('aria-pressed', String(!visible));
  });
});
