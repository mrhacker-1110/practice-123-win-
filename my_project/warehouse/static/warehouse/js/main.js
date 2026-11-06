document.addEventListener('DOMContentLoaded', () => {
  const consultantForm = document.querySelector('#consultant-form');
  if (consultantForm) {
    const input = consultantForm.querySelector('#consultant-input');
    const attachmentInput = consultantForm.querySelector('#consultant-attachment');
    const photoInput = consultantForm.querySelector('#consultant-photo');
    const attachmentName = consultantForm.querySelector('#consultant-attachment-name');
    const sendButton = consultantForm.querySelector('#consultant-send');
    const transcript = document.querySelector('#consultant-messages');
    const status = document.querySelector('#consultant-status');
    const addBubble = (text, role, fileName = '', createdAt = new Date().toISOString()) => {
      const date = new Date(createdAt);
      const dayKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      const daySeparators = transcript.querySelectorAll('.consultant-day-separator');
      const lastDay = daySeparators[daySeparators.length - 1];
      if (!lastDay || lastDay.dataset.chatDate !== dayKey) {
        const separator = document.createElement('div');
        separator.className = 'consultant-day-separator';
        separator.dataset.chatDate = dayKey;
        const dayLabel = document.createElement('span');
        dayLabel.textContent = new Intl.DateTimeFormat(document.documentElement.lang === 'ru' ? 'ru-RU' : 'en', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
        separator.appendChild(dayLabel);
        transcript.appendChild(separator);
      }
      const bubble = document.createElement('div');
      bubble.className = `consultant-message consultant-message-${role}`;
      const content = document.createElement('span');
      content.textContent = text;
      bubble.appendChild(content);
      if (fileName) {
        const fileLabel = document.createElement('span');
        fileLabel.className = 'consultant-attachment-chip';
        fileLabel.textContent = `📎 ${fileName}`;
        bubble.appendChild(fileLabel);
      }
      const time = document.createElement('time');
      time.className = 'consultant-message-time';
      time.dateTime = date.toISOString();
      time.textContent = new Intl.DateTimeFormat(document.documentElement.lang === 'ru' ? 'ru-RU' : 'en', { hour: '2-digit', minute: '2-digit' }).format(date);
      bubble.appendChild(time);
      transcript.appendChild(bubble);
      transcript.scrollTop = transcript.scrollHeight;
      return bubble;
    };
    const selectedFile = () => (photoInput.files && photoInput.files[0]) || (attachmentInput.files && attachmentInput.files[0]);
    photoInput.addEventListener('change', () => {
      if (photoInput.files.length) attachmentInput.value = '';
      const file = selectedFile();
      attachmentName.textContent = file ? file.name : '';
    });
    attachmentInput.addEventListener('change', () => {
      if (attachmentInput.files.length) photoInput.value = '';
      const file = selectedFile();
      attachmentName.textContent = file ? file.name : '';
    });
    consultantForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = input.value.trim();
      const file = selectedFile();
      if (!message && !file) return;
      const displayedMessage = message || (document.documentElement.lang === 'ru' ? 'Проанализируй этот файл' : 'Please analyze this file');
      const userBubble = addBubble(displayedMessage, 'user', file ? file.name : '');
      const body = new FormData(consultantForm);
      input.value = '';
      input.disabled = true;
      attachmentInput.disabled = true;
      photoInput.disabled = true;
      sendButton.disabled = true;
      sendButton.setAttribute('aria-busy', 'true');
      status.textContent = document.documentElement.lang === 'ru' ? 'Консультант думает…' : 'The consultant is thinking…';
      try {
        const response = await fetch(consultantForm.dataset.endpoint, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'same-origin',
          body,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'The consultant could not answer.');
        userBubble.querySelector('.consultant-message-time').dateTime = result.user_created_at;
        userBubble.querySelector('.consultant-message-time').textContent = new Intl.DateTimeFormat(document.documentElement.lang === 'ru' ? 'ru-RU' : 'en', { hour: '2-digit', minute: '2-digit' }).format(new Date(result.user_created_at));
        addBubble(result.answer, 'model', '', result.answer_created_at);
        attachmentInput.value = '';
        photoInput.value = '';
        attachmentName.textContent = '';
        const activeChat = document.querySelector('.chat-history-item.is-active');
        if (activeChat && result.title) {
          const chatLink = activeChat.querySelector('a');
          chatLink.textContent = result.title;
          chatLink.title = result.title;
          activeChat.dataset.title = result.title.toLocaleLowerCase();
        }
        status.textContent = '';
      } catch (error) {
        userBubble.remove();
        input.value = message;
        status.textContent = error.message;
      } finally {
        input.disabled = false;
        attachmentInput.disabled = false;
        photoInput.disabled = false;
        sendButton.disabled = false;
        sendButton.removeAttribute('aria-busy');
        input.focus();
      }
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        consultantForm.requestSubmit();
      }
    });
  }
  const chatSearch = document.querySelector('#chat-search');
  const chatItems = [...document.querySelectorAll('[data-chat-item]')];
  const searchEmpty = document.querySelector('#chat-search-empty');
  if (chatSearch) {
    chatSearch.addEventListener('input', () => {
      const query = chatSearch.value.trim().toLocaleLowerCase();
      let visibleCount = 0;
      chatItems.forEach((item) => {
        const matches = (item.dataset.title || item.textContent).toLocaleLowerCase().includes(query);
        item.hidden = !matches;
        if (matches) visibleCount += 1;
      });
      if (searchEmpty) searchEmpty.hidden = !query || visibleCount > 0;
    });
  }
  document.querySelectorAll('.chat-delete-form').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const prompt = form.dataset.confirm;
      if (prompt && !window.confirm(prompt)) event.preventDefault();
    });
  });
  const photoInput = document.querySelector('#product-photo');
  const photoButton = document.querySelector('#analyze-product-photo');
  const photoPreview = document.querySelector('#product-photo-preview');
  const photoStatus = document.querySelector('#photo-analysis-status');
  if (photoInput && photoButton && photoPreview && photoStatus) {
    let previewUrl = '';
    photoInput.addEventListener('change', () => {
      const file = photoInput.files && photoInput.files[0];
      photoButton.disabled = !file;
      photoStatus.textContent = '';
      photoStatus.classList.remove('is-error');
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      if (!file) {
        photoPreview.hidden = true;
        photoPreview.removeAttribute('src');
        return;
      }
      previewUrl = URL.createObjectURL(file);
      photoPreview.src = previewUrl;
      photoPreview.hidden = false;
    });
    photoButton.addEventListener('click', async () => {
      const file = photoInput.files && photoInput.files[0];
      if (!file) return;
      const form = photoInput.closest('form');
      const body = new FormData();
      body.append('photo', file);
      const csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
      photoButton.disabled = true;
      photoButton.setAttribute('aria-busy', 'true');
      photoStatus.textContent = document.documentElement.lang === 'ru' ? 'Анализируем фото…' : 'Analyzing photo…';
      try {
        const response = await fetch(photoButton.dataset.endpoint, {
          method: 'POST',
          body,
          headers: { 'X-CSRFToken': csrf.value, 'X-Requested-With': 'XMLHttpRequest' },
          credentials: 'same-origin',
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Photo analysis failed.');
        const fields = {
          name: 'id_name', sku: 'id_sku', description: 'id_description',
          quantity: 'id_quantity', unit_price: 'id_unit_price', minimum_stock: 'id_minimum_stock',
        };
        Object.entries(fields).forEach(([key, id]) => {
          const input = document.getElementById(id);
          const value = result.suggestions[key];
          if (input && value && !input.disabled) {
            input.value = value;
            input.classList.add('ai-suggested');
            input.dispatchEvent(new Event('input', { bubbles: true }));
          }
        });
        photoStatus.textContent = document.documentElement.lang === 'ru'
          ? 'Поля заполнены предложениями. Проверьте их перед сохранением.'
          : 'Suggestions filled in. Review them before saving.';
        photoStatus.classList.remove('is-error');
        document.getElementById('id_name')?.focus();
      } catch (error) {
        photoStatus.textContent = error.message;
        photoStatus.classList.add('is-error');
      } finally {
        photoButton.disabled = false;
        photoButton.removeAttribute('aria-busy');
      }
    });
  }
  const themeSelect = document.querySelector('#theme-select');
  const validThemes = ['system', 'light', 'dark'];
  let savedTheme = 'system';
  try {
    savedTheme = localStorage.getItem('warehouse-theme') || 'system';
  } catch (error) {
    if (!(error instanceof DOMException)) {
      throw error;
    }
  }
  const initialTheme = validThemes.includes(savedTheme) ? savedTheme : 'system';

  document.documentElement.dataset.theme = initialTheme;
  if (themeSelect) {
    themeSelect.value = initialTheme;
    themeSelect.addEventListener('change', () => {
      document.documentElement.dataset.theme = themeSelect.value;
      try {
        localStorage.setItem('warehouse-theme', themeSelect.value);
      } catch (error) {
        if (!(error instanceof DOMException)) {
          throw error;
        }
      }
    });
  }

  const menuToggle = document.querySelector('.nav-toggle');
  const navigation = document.querySelector('#site-navigation');
  if (menuToggle && navigation) {
    menuToggle.addEventListener('click', () => {
      const expanded = menuToggle.getAttribute('aria-expanded') === 'true';
      menuToggle.setAttribute('aria-expanded', String(!expanded));
      navigation.classList.toggle('nav-open', !expanded);
    });
    navigation.addEventListener('click', (event) => {
      if (event.target.closest('a')) {
        menuToggle.setAttribute('aria-expanded', 'false');
        navigation.classList.remove('nav-open');
      }
    });
  }

  const salesPeriodSelect = document.querySelector('#sales-period-select');
  const customDateFields = document.querySelector('#custom-date-fields');
  if (salesPeriodSelect && customDateFields) {
    const dateInputs = customDateFields.querySelectorAll('input[type="date"]');
    const updateDateFields = () => {
      const needsCustomDates = salesPeriodSelect.value === 'custom';
      customDateFields.hidden = !needsCustomDates;
      dateInputs.forEach((input) => {
        input.required = needsCustomDates;
      });
    };
    salesPeriodSelect.addEventListener('change', updateDateFields);
    updateDateFields();
  }

  document.querySelectorAll('form[data-live-validation]').forEach((form) => {
    const fields = form.querySelectorAll('input:not([type="hidden"]):not([type="submit"]), select, textarea');
    const validLabel = form.dataset.validLabel;
    const invalidLabel = form.dataset.invalidLabel;
    const passwordField = form.querySelector('input[name="password1"]');
    const passwordStrengthBar = document.getElementById('password-strength-bar');
    const passwordStrengthLabel = document.getElementById('password-strength-label');
    const passwordRules = document.getElementById('password-rules');

    const getPasswordRuleState = (value) => ({
      min_length: value.length >= 8,
      has_letter: /[A-Za-zА-Яа-яЁё]/.test(value),
      has_digit: /\d/.test(value),
      not_only_digits: /\D/.test(value),
    });

    const updatePasswordRules = () => {
      if (!passwordField || !passwordRules) {
        return;
      }
      const value = passwordField.value;
      const rules = getPasswordRuleState(value);
      const ruleItems = passwordRules.querySelectorAll('[data-rule]');
      let allValid = true;
      ruleItems.forEach((item) => {
        const ruleName = item.dataset.rule;
        const passed = Boolean(rules[ruleName]);
        item.classList.toggle('is-valid', passed);
        item.classList.toggle('is-invalid', !passed && value.length > 0);
        if (!passed) {
          allValid = false;
        }
      });

      passwordRules.classList.toggle('hidden', allValid && value.length > 0);
      const toggleButton = passwordRules.querySelector('.password-rules-toggle');
      if (toggleButton) {
        toggleButton.textContent = passwordRules.classList.contains('hidden')
          ? (document.documentElement.lang === 'ru' ? 'Показать' : 'Show')
          : (document.documentElement.lang === 'ru' ? 'Скрыть' : 'Hide');
        toggleButton.setAttribute('aria-expanded', String(!passwordRules.classList.contains('hidden')));
      }
    };

    const updatePasswordStrength = () => {
      if (!passwordField || !passwordStrengthBar || !passwordStrengthLabel) {
        return;
      }

      const value = passwordField.value;
      const rules = getPasswordRuleState(value);
      const total = Object.values(rules).filter(Boolean).length;

      let level = 'weak';
      let label = 'Weak';
      let width = '20%';

      if (value.length === 0) {
        width = '0%';
        label = 'Not checked';
      } else if (total <= 1) {
        width = '25%';
        label = 'Weak';
        level = 'weak';
      } else if (total === 2) {
        width = '50%';
        label = 'Medium';
        level = 'medium';
      } else if (total === 3) {
        width = '75%';
        label = 'Strong';
        level = 'strong';
      } else {
        width = '100%';
        label = 'Very strong';
        level = 'very-strong';
      }

      const russianMode = document.documentElement.lang === 'ru' || document.body.textContent.includes('Русский');
      if (russianMode) {
        const localized = {
          'Not checked': 'Не оценено',
          'Weak': 'Слабый',
          'Medium': 'Средний',
          'Strong': 'Надёжный',
          'Very strong': 'Очень надёжный',
        };
        label = localized[label] || label;
      }

      passwordStrengthBar.style.width = width;
      passwordStrengthBar.dataset.level = level;
      passwordStrengthLabel.textContent = `${russianMode ? 'Надёжность пароля' : 'Password strength'}: ${label}`;
      updatePasswordRules();
    };

    if (passwordField) {
      passwordField.addEventListener('input', updatePasswordStrength);
      updatePasswordStrength();
    }

    document.querySelectorAll('.password-toggle-btn').forEach((button) => {
      button.addEventListener('click', () => {
        const target = document.getElementById(button.dataset.passwordTarget);
        if (!target) {
          return;
        }
        const isPassword = target.type === 'password';
        target.type = isPassword ? 'text' : 'password';
        button.textContent = isPassword
          ? (document.documentElement.lang === 'ru' ? 'Скрыть' : 'Hide')
          : (document.documentElement.lang === 'ru' ? 'Показать' : 'Show');
        button.setAttribute('aria-label', isPassword ? (document.documentElement.lang === 'ru' ? 'Скрыть пароль' : 'Hide password') : (document.documentElement.lang === 'ru' ? 'Показать пароль' : 'Show password'));
      });
    });

    const passwordRulesToggle = document.querySelector('.password-rules-toggle');
    if (passwordRulesToggle && passwordRules) {
      passwordRulesToggle.addEventListener('click', () => {
        const hidden = passwordRules.classList.contains('hidden');
        passwordRules.classList.toggle('hidden', !hidden);
        passwordRulesToggle.textContent = hidden
          ? (document.documentElement.lang === 'ru' ? 'Скрыть' : 'Hide')
          : (document.documentElement.lang === 'ru' ? 'Показать' : 'Show');
        passwordRulesToggle.setAttribute('aria-expanded', String(hidden));
      });
    }

    form.addEventListener('submit', () => {
      const submitButton = form.querySelector('button[type="submit"]');
      if (!submitButton || !form.dataset.submittingLabel) {
        return;
      }
      submitButton.disabled = true;
      submitButton.setAttribute('aria-busy', 'true');
      submitButton.textContent = form.dataset.submittingLabel;
    });

    const updateField = (field) => {
      const wrapper = field.closest('.field');
      if (!wrapper) {
        return;
      }
      field.dataset.touched = 'true';
      const value = field.value.trim();

      if (field.type === 'password' && field.name === 'password2') {
        const password = form.querySelector('input[name="password1"]');
        field.setCustomValidity(
          field.value && password && field.value !== password.value
            ? invalidLabel
            : '',
        );
      }

      if (field.name === 'phone' && value) {
        field.setCustomValidity(/^[+]?\d[\d\s\-\(\)]{6,29}$/.test(value) ? '' : invalidLabel);
      }

      const valid = value
        ? field.checkValidity() && !(field.name === 'phone' && !/^[+]?\d[\d\s\-\(\)]{6,29}$/.test(value))
        : false;
      wrapper.classList.toggle('field-valid', valid);
      wrapper.classList.toggle('field-invalid', !valid && value !== '');
      let feedback = wrapper.querySelector('.field-feedback');
      if (!feedback) {
        feedback = document.createElement('span');
        feedback.className = 'field-feedback';
        feedback.setAttribute('aria-live', 'polite');
        wrapper.append(feedback);
      }
      feedback.textContent = valid ? validLabel : value ? invalidLabel : '';
    };

    fields.forEach((field) => {
      field.addEventListener('blur', () => updateField(field));
      field.addEventListener('input', () => {
        updateField(field);
        if (field.name === 'password1') {
          const confirmation = form.querySelector('input[name="password2"]');
          if (confirmation && confirmation.dataset.touched) {
            updateField(confirmation);
          }
        }
      });
    });
  });
});
