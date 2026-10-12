(() => {
  const commonPasswords = new Set([
    "password", "password1", "12345678", "123456789", "qwerty123", "letmein",
    "welcome1", "admin123", "iloveyou", "naijaclip", "football", "monkey", "dragon",
  ]);
  const simpleSequences = ["012345", "123456", "234567", "345678", "456789", "abcdef", "qwerty", "asdfgh"];

  const strengthFor = (password) => {
    if (!password) return { level: 0, label: "enter a password" };
    const normalized = password.toLowerCase();
    const hasSimplePattern = commonPasswords.has(normalized)
      || /(password|welcome|admin|qwerty|letmein|iloveyou|naijaclip)/.test(normalized)
      || /^(.)\1+$/.test(normalized)
      || /(.{1,4})\1{2,}/i.test(password)
      || simpleSequences.some((sequence) => normalized.includes(sequence));
    if (password.length < 8 || hasSimplePattern) return { level: 1, label: "Weak" };

    const characterGroups = [/[a-z]/.test(password), /[A-Z]/.test(password), /\d/.test(password), /[^A-Za-z0-9]/.test(password)]
      .filter(Boolean).length;
    if (password.length < 12) return { level: 2, label: "Fair" };
    if (password.length >= 16 && characterGroups >= 3) return { level: 4, label: "Strong" };
    if (characterGroups >= 2) return { level: 3, label: "Good" };
    return { level: 2, label: "Fair" };
  };

  document.querySelectorAll("#password, #new-password").forEach((input) => {
    const container = input.closest(".nc-field, .nc-reset-field");
    const strength = container?.querySelector(".nc-password-strength");
    const label = strength?.querySelector("span[id$='strength-label']");
    const progress = strength?.querySelector("[role='progressbar']");
    const bar = progress?.querySelector("span");
    if (!strength || !label || !progress || !bar) return;

    const update = () => {
      const result = strengthFor(input.value);
      label.textContent = result.level ? `Password strength: ${result.label}` : "Password strength: enter a password";
      progress.setAttribute("aria-valuenow", String(result.level));
      progress.setAttribute("aria-valuetext", result.level ? result.label : "No password entered");
      strength.dataset.level = String(result.level);
      bar.style.width = `${result.level * 25}%`;
    };

    input.addEventListener("input", update);
    update();
  });
})();