(() => {
  const page = document.querySelector('[data-reset-page]')?.dataset.resetPage;
  const status = document.getElementById('reset-status');
  const emailKey = 'naijaclip-reset-email';
  const cooldownKey = 'naijaclip-reset-resend-at';
  const cooldownSeconds = 45;

  const showStatus = (message, kind = 'error') => {
    if (!status) return;
    status.textContent = message;
    status.dataset.kind = kind;
    status.hidden = false;
  };

  const setBusy = (button, busy, label) => {
    if (!button) return;
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.disabled = true;
      button.innerHTML = `<span class="nc-loader" aria-hidden="true"></span>${label}`;
    } else {
      button.disabled = false;
      button.textContent = button.dataset.originalText || label;
    }
  };

  const readResponse = async (response) => response.json().catch(() => ({}));

  if (page === 'forgot') {
    document.getElementById('forgot-password-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const email = document.getElementById('reset-email').value.trim();
      const button = form.querySelector('button[type="submit"]');
      status.hidden = true;
      setBusy(button, true, 'Sending code...');
      try {
        const response = await fetch('/auth/forgot-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        });
        const data = await readResponse(response);
        if (!response.ok) throw new Error(data.detail || 'Unable to send a code right now.');
        if (data.google_signin) {
          showStatus(data.detail, 'error');
          return;
        }
        localStorage.setItem(emailKey, email);
        localStorage.setItem(cooldownKey, String(Date.now() + (data.resend_after_seconds || cooldownSeconds) * 1000));
        window.location.assign('/verify-reset-code');
      } catch (error) {
        showStatus(error.message || 'We could not reach the server. Try again.');
      } finally {
        setBusy(button, false, 'Send code');
      }
    });
  }

  if (page === 'verify') {
    const emailInput = document.getElementById('reset-email');
    emailInput.value = localStorage.getItem(emailKey) || '';

    const digits = [...document.querySelectorAll('.nc-otp-digit')];
    const readCode = () => digits.map((input) => input.value).join('');
    digits.forEach((input, index) => {
      input.addEventListener('input', () => {
        const entered = input.value.replace(/\D/g, '');
        if (entered.length > 1) {
          entered.slice(0, digits.length - index).split('').forEach((digit, offset) => {
            digits[index + offset].value = digit;
          });
          digits[Math.min(index + entered.length, digits.length - 1)].focus();
          return;
        }
        input.value = entered;
        if (input.value && digits[index + 1]) digits[index + 1].focus();
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Backspace' && !input.value && digits[index - 1]) digits[index - 1].focus();
        if (event.key === 'ArrowLeft' && digits[index - 1]) digits[index - 1].focus();
        if (event.key === 'ArrowRight' && digits[index + 1]) digits[index + 1].focus();
      });
      input.addEventListener('paste', (event) => {
        const pasted = event.clipboardData.getData('text').replace(/\D/g, '').slice(0, digits.length - index);
        if (!pasted) return;
        event.preventDefault();
        pasted.split('').forEach((digit, offset) => { digits[index + offset].value = digit; });
        digits[Math.min(index + pasted.length, digits.length - 1)].focus();
      });
    });

    const resendButton = document.getElementById('resend-reset-code');
    const countdown = document.getElementById('resend-countdown');
    const updateCooldown = () => {
      const resendAt = Number(localStorage.getItem(cooldownKey) || 0);
      const remaining = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
      resendButton.disabled = remaining > 0;
      countdown.textContent = remaining ? `Resend code in ${remaining} seconds` : 'You can request another code.';
    };
    updateCooldown();
    window.setInterval(updateCooldown, 1000);

    document.getElementById('verify-reset-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = emailInput.value.trim();
      const code = readCode();
      if (!email) {
        showStatus('Enter the email address that received the code.');
        return;
      }
      if (!/^\d{6}$/.test(code)) {
        showStatus('Enter all six digits of the verification code.');
        return;
      }
      const button = event.currentTarget.querySelector('button[type="submit"]');
      status.hidden = true;
      setBusy(button, true, 'Verifying...');
      try {
        const response = await fetch('/auth/verify-reset-code', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ email, code }),
        });
        const data = await readResponse(response);
        if (!response.ok) throw new Error(data.detail || 'The verification code could not be checked.');
        localStorage.setItem(emailKey, email);
        window.location.assign('/reset-password');
      } catch (error) {
        showStatus(error.message || 'We could not reach the server. Try again.');
      } finally {
        setBusy(button, false, 'Verify code');
      }
    });

    resendButton.addEventListener('click', async () => {
      const email = emailInput.value.trim();
      if (!email) {
        showStatus('Enter the email address that received the code.');
        return;
      }
      status.hidden = true;
      setBusy(resendButton, true, 'Sending...');
      try {
        const response = await fetch('/auth/resend-reset-code', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        });
        const data = await readResponse(response);
        if (!response.ok) throw new Error(data.detail || 'Unable to resend the code.');
        if (data.google_signin) throw new Error(data.detail);
        localStorage.setItem(cooldownKey, String(Date.now() + (data.resend_after_seconds || cooldownSeconds) * 1000));
        updateCooldown();
        showStatus(data.detail, 'success');
      } catch (error) {
        showStatus(error.message || 'We could not reach the server. Try again.');
      } finally {
        setBusy(resendButton, false, 'Resend code');
        updateCooldown();
      }
    });

    const expired = new URLSearchParams(window.location.search).get('expired');
    if (expired) showStatus('Your reset authorization expired. Verify a new code to continue.');
  }

  if (page === 'reset') {
    document.querySelectorAll('.nc-password-toggle').forEach((button) => {
      button.addEventListener('click', () => {
        const input = document.getElementById(button.dataset.passwordTarget);
        const visible = input.type === 'text';
        input.type = visible ? 'password' : 'text';
        button.textContent = visible ? 'Show' : 'Hide';
        button.setAttribute('aria-label', `${visible ? 'Show' : 'Hide'} ${input.id === 'new-password' ? 'new password' : 'confirmation password'}`);
      });
    });

    document.getElementById('reset-password-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const password = document.getElementById('new-password').value;
      const confirmation = document.getElementById('confirm-password').value;
      if (password.length < 8) {
        showStatus('Your password must be at least 8 characters long.');
        return;
      }
      if (password !== confirmation) {
        showStatus('The passwords do not match.');
        return;
      }
      const button = form.querySelector('button[type="submit"]');
      status.hidden = true;
      setBusy(button, true, 'Updating password...');
      try {
        const response = await fetch('/auth/reset-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ new_password: password }),
        });
        const data = await readResponse(response);
        if (!response.ok) throw new Error(data.detail || 'Unable to reset your password.');
        form.hidden = true;
        document.getElementById('reset-success').hidden = false;
        localStorage.removeItem(emailKey);
        localStorage.removeItem(cooldownKey);
        localStorage.removeItem('naijaclip_access_token');
        localStorage.removeItem('naijaclip_refresh_token');
        localStorage.removeItem('naijaclip_user_email');
      } catch (error) {
        showStatus(error.message || 'We could not reach the server. Try again.');
      } finally {
        setBusy(button, false, 'Reset password');
      }
    });
  }
})();