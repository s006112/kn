// Frontend controller for quote/panelizer forms; invoked by layout.html after templates render.
// Calls backend routes via fetch, syncing DOM sections and default tracking consistent with app_q services.

(function () {
  const quoteForm = document.getElementById('quote-form');
  const panelForm = document.getElementById('panelizer-form');
  const liveSummary = document.getElementById('live-summary');
  const panelizerResults = document.getElementById('panelizer-results');

  const parseJSON = (id, fallback) => {
    const element = document.getElementById(id);
    if (!element) return fallback;
    try {
      const parsed = JSON.parse(element.textContent || 'null');
      return parsed == null ? fallback : parsed;
    } catch (error) {
      console.error(`Failed to parse ${id}`, error);
      return fallback;
    }
  };

  const pricedCosts = parseJSON('priced-costs-data', {});
  const pricedFields = parseJSON('priced-fields-config', []);
  const stackQtyMap = parseJSON('stack-qty-map', {});
  if (!quoteForm && !panelForm) return;

  let controller;

  const updateDOM = (htmlText) => {
    const doc = new DOMParser().parseFromString(htmlText, 'text/html');
    if (liveSummary) {
      const newSummary = doc.getElementById('live-summary');
      if (newSummary) liveSummary.innerHTML = newSummary.innerHTML;
    }
    if (panelizerResults) {
      const newResults = doc.getElementById('panelizer-results');
      if (newResults) panelizerResults.innerHTML = newResults.innerHTML;
    }
    if (quoteForm) {
      const panelBoardsInput = quoteForm.querySelector('input[name="panel_boards"]');
      const newPanelBoardsInput = doc.querySelector('#quote-form input[name="panel_boards"]');
      if (panelBoardsInput && newPanelBoardsInput) {
        panelBoardsInput.value = newPanelBoardsInput.value;
      }
    }
  };

  const submitForm = () => {
    const targetForm = quoteForm || panelForm;
    if (!targetForm) return;

    const formData = new FormData(targetForm);
    if (quoteForm && panelForm) {
      new FormData(panelForm).forEach((value, key) => formData.set(key, value));
    }

    if (controller) controller.abort();
    controller = new AbortController();

    fetch(targetForm.action || window.location.pathname, {
      method: 'POST',
      body: new URLSearchParams(formData),
      headers: { 'X-Requested-With': 'fetch' },
      signal: controller.signal,
    })
      .then((response) => response.text())
      .then(updateDOM)
      .catch((error) => {
        if (error.name !== 'AbortError') console.error('Fetch failed', error);
      });
  };

  const debouncedSubmit = (() => {
    let timer;
    return () => {
      clearTimeout(timer);
      timer = setTimeout(submitForm, 200);
    };
  })();

  [quoteForm, panelForm].filter(Boolean).forEach((formElement) => {
    formElement.addEventListener('input', debouncedSubmit);
    formElement.addEventListener('change', debouncedSubmit);
    formElement.addEventListener('submit', (event) => {
      event.preventDefault();
      submitForm();
    });
  });

  const normalizeString = (value) => (value == null ? '' : String(value).trim());
  const parseNumber = (value) => {
    if (value == null || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  };
  const toBoolean = (value) => {
    if (value === undefined) return false;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
      const trimmed = value.trim().toLowerCase();
      if (!trimmed) return false;
      return trimmed === 'true' || trimmed === '1' || trimmed === 'yes' || trimmed === 'on';
    }
    return Boolean(value);
  };
  const valuesEqual = (el, original) => {
    if (el.type === 'checkbox') {
      return toBoolean(original) === el.checked;
    }
    const currentNum = parseNumber(el.value);
    const originalNum = parseNumber(original);
    if (el.type === 'number' && currentNum !== null && originalNum !== null) {
      return currentNum === originalNum;
    }
    return normalizeString(el.value) === normalizeString(original);
  };

  const initChangeTracking = (form) => {
    if (!form) return null;
    const changeTrackers = new Map();
    const defaultResolvers = Object.create(null);
    let defaultsObj = null;
    const defaultsJson = form.dataset.defaultJson;
    if (defaultsJson) {
      try {
        defaultsObj = JSON.parse(defaultsJson);
      } catch (error) {
        console.error(`Failed to parse defaults for ${form.id}`, error);
      }
    }

    const refreshField = (name) => {
      if (!name) return;
      const syncs = changeTrackers.get(name);
      if (syncs) syncs.forEach((fn) => fn());
    };

    const getOriginalValue = (el) => {
      if (!el || !el.name) return undefined;
      const resolver = defaultResolvers[el.name];
      if (resolver) return resolver();
      if (defaultsObj && Object.prototype.hasOwnProperty.call(defaultsObj, el.name)) {
        return defaultsObj[el.name];
      }
      return undefined;
    };

    const updateClass = (el) => {
      const original = getOriginalValue(el);
      if (original === undefined) {
        el.classList.remove('changed');
        return;
      }
      el.classList.toggle('changed', !valuesEqual(el, original));
    };

    const trackElement = (el) => {
      const sync = () => updateClass(el);
      el.addEventListener('input', sync);
      el.addEventListener('change', sync);
      sync();
      if (!el.name) return;
      const entries = changeTrackers.get(el.name) || [];
      entries.push(sync);
      changeTrackers.set(el.name, entries);
    };

    form.querySelectorAll('input[name], select[name]').forEach(trackElement);

    return {
      registerDefaultResolver: (name, resolver) => {
        defaultResolvers[name] = resolver;
        refreshField(name);
      },
      refreshField,
      getChangedFields: () =>
        Array.from(form.querySelectorAll('.changed[name]')).map((el) => el.name),
    };
  };

  const quoteTracker = initChangeTracking(quoteForm);
  const panelTracker = initChangeTracking(panelForm);
  const persistButton = document.getElementById('persist-defaults');

  if (persistButton && (quoteForm || panelForm)) {
    const setButtonDisabled = (state) => {
      persistButton.disabled = state;
    };

    persistButton.addEventListener('click', () => {
      const changedFields = [
        ...(quoteTracker?.getChangedFields() ?? []),
        ...(panelTracker?.getChangedFields() ?? []),
      ];
      const uniqueFields = Array.from(new Set(changedFields));
      if (!uniqueFields.length) {
        alert('没有检测到变化。');
        return;
      }
      const baseForm = quoteForm || panelForm;
      if (!baseForm) return;
      const formData = new FormData(baseForm);
      if (quoteForm && panelForm) {
        new FormData(panelForm).forEach((value, key) => formData.set(key, value));
      }
      formData.set('persist_defaults', '1');
      formData.append('changed_fields', uniqueFields.join(','));
      setButtonDisabled(true);
      const targetAction =
        (quoteForm && quoteForm.action) ||
        (panelForm && panelForm.action) ||
        window.location.pathname;
      fetch(targetAction || window.location.pathname, {
        method: 'POST',
        body: new URLSearchParams(formData),
        headers: { 'X-Requested-With': 'fetch' },
      })
        .then(() => {
          setButtonDisabled(false);
          window.location.reload();
        })
        .catch(() => {
          setButtonDisabled(false);
        });
    });
  }

  if (quoteForm && quoteTracker) {
    const toDash = (s) => s.replace(/_/g, '-');
    const getDefault = (name) =>
      quoteForm.dataset[`default${name[0].toUpperCase()}${name.slice(1)}`] || '';
    const getId = (base) => document.getElementById(base);
    const substrateSel = getId('substrate-thickness-select');
    const cuSel = getId('cu-thickness-select');
    const defaultSubstrate = getDefault('substrate_thickness');
    const defaultCu = getDefault('cu_thickness');
    let materialFieldSync;

    pricedFields.forEach(({ name, priceField }) => {
      const sel = getId(`${name}-select`);
      const inp = getId(toDash(priceField) + '-input');
      const lbl = getId(toDash(priceField) + '-label');
      if (!sel || !inp || !lbl) return;

      const isMaterialField = name === 'material';
      const getSelection = () => sel.value || getDefault(name);
      const computeDefaultPrice = () => {
        const selected = getSelection();
        if (!selected) return '';
        if (isMaterialField) {
          const substrate = substrateSel?.value || defaultSubstrate;
          const cu = cuSel?.value || defaultCu;
          return pricedCosts[name]?.[selected]?.[substrate]?.[cu] ?? '';
        }
        return pricedCosts[name]?.[selected] ?? '';
      };

      quoteTracker.registerDefaultResolver(priceField, computeDefaultPrice);

      const sync = (force) => {
        const optionLabel = getSelection();
        lbl.textContent = optionLabel;
        if (force || !inp.value) {
          const priceValue = computeDefaultPrice();
          inp.value = priceValue === undefined ? '' : priceValue;
        }
        quoteTracker.refreshField(priceField);
      };

      sel.addEventListener('change', () => sync(true));
      sync();

      if (isMaterialField) {
        materialFieldSync = sync;
      }
    });

    if (materialFieldSync) {
      [substrateSel, cuSel].filter(Boolean).forEach((select) => {
        select.addEventListener('change', () => materialFieldSync(true));
      });
    }

    const thickSel = getId('pcb-thickness-select');
    const holesSel = getId('cnc-hole-select');
    const stackInp = getId('stack-qty-input');
    if (thickSel && holesSel && stackInp) {
      const updateStack = () => {
        const derived = stackQtyMap[thickSel.value]?.[holesSel.value];
        if (derived) stackInp.value = derived;
      };
      thickSel.addEventListener('change', updateStack);
      holesSel.addEventListener('change', updateStack);
      updateStack();
    }
  }

  submitForm();
})();
