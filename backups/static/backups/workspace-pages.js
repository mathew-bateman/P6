(() => {
    'use strict';
    if (!document.body.classList.contains('workspace-app') || !window.HTMLDialogElement) return;

    const assetBase = new URL('.', document.currentScript.src);
    const panelSelector = '.overview-filters, .validation-filter-panel, .validation-filters, .portfolio-filters';
    const states = new Set();
    const enhanced = new WeakMap();
    const reportFilterKeys = ['portfolio', 'project', 'lead_planner', 'project_status', 'project_state', 'discipline', 'project_phase', 'updated_date', 'exclude_blanks', 'check'];
    const reportSources = new Set(['combined', 'p6_22', 'p6_23']);
    let serial = 0;

    const synchronizeSidebar = () => {
        if (!['quality', 'portfolio'].includes(document.body.dataset.workspaceActiveSection)) return;
        const applied = new URLSearchParams(location.search);
        document.querySelectorAll('#workspace-sidebar .workspace-tree-link').forEach(link => {
            const destination = new URL(link.href, location.href);
            if (destination.origin !== location.origin) return;
            const source = [applied.get('report_source'), destination.searchParams.get('report_source')].find(value => reportSources.has(value));
            const query = new URLSearchParams();
            if (source) query.set('report_source', source);
            reportFilterKeys.forEach(key => applied.getAll(key).slice(0, 16).forEach(value => query.append(key, value.slice(0, 200))));
            destination.search = query.toString();
            link.setAttribute('href', `${destination.pathname}${destination.search}${destination.hash}`);
        });
    };

    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        if (text) node.textContent = text;
        return node;
    };
    const button = (className, label, icon) => {
        const node = element('button', className, icon ? '' : label);
        node.type = 'button';
        if (icon) {
            node.setAttribute('aria-label', label);
            node.title = label;
            const image = element('span', 'workspace-icon');
            image.setAttribute('aria-hidden', 'true');
            image.style.setProperty('--workspace-icon', `url("${new URL(`icons/${icon}.svg`, assetBase)}")`);
            node.append(image);
        }
        return node;
    };
    const fields = form => Array.from(form.elements).filter(control => control.name && !['submit', 'button', 'reset'].includes(control.type));
    const snapshot = form => fields(form).map(control => ({
        control,
        values: control.tagName === 'SELECT' ? Array.from(control.selectedOptions, option => option.value) : null,
        value: control.value,
        checked: control.checked,
    }));
    const setValue = (control, value) => {
        if (control.tomselect) control.tomselect.setValue(value, true);
        else if (control.tagName === 'SELECT') {
            const values = Array.isArray(value) ? value : [value];
            Array.from(control.options).forEach(option => { option.selected = values.includes(option.value); });
        } else if (control._flatpickr) {
            if (value) control._flatpickr.setDate(value, false, 'Y-m-d');
            else control._flatpickr.clear(false);
        } else control.value = value;
    };
    const restore = state => {
        state.restoring = true;
        state.applied.forEach(({ control, values, value, checked }) => {
            if (!state.form.contains(control)) return;
            setValue(control, values ?? value);
            if (['checkbox', 'radio'].includes(control.type)) control.checked = checked;
        });
        state.restoring = false;
    };
    const closeWidgets = state => {
        state.form.querySelectorAll('select').forEach(select => select.tomselect?.close());
        state.form.querySelectorAll('.schedule-quality-date-picker').forEach(input => input._flatpickr?.close());
    };
    const close = (state, reason = 'cancel') => {
        closeWidgets(state);
        if (reason === 'cancel') restore(state);
        if (state.dialog.open) state.dialog.close(reason);
        state.toggle.setAttribute('aria-expanded', 'false');
    };
    const positionCalendars = state => {
        for (const { instance, input, calendar } of state.calendars.values()) {
            if (!instance.isOpen || !state.dialog.open) continue;
            const box = (instance.altInput || input).getBoundingClientRect();
            const width = Math.min(344, window.innerWidth - 24);
            calendar.style.position = 'fixed';
            calendar.style.width = `${width}px`;
            calendar.style.right = 'auto';
            calendar.style.bottom = 'auto';
            calendar.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;
            const height = calendar.getBoundingClientRect().height || 340;
            const below = box.bottom + 6;
            calendar.style.top = `${below + height < window.innerHeight - 8 ? below : Math.max(8, box.top - height - 6)}px`;
        }
    };
    const prepareWidgets = state => {
        // Keep the existing options/renderers, but remove the editable search
        // control. Native selects remain a working fallback if a widget fails.
        state.form.querySelectorAll('select.schedule-quality-select, select.portfolio-select').forEach(select => {
            const current = select.tomselect;
            if (!window.TomSelect || current?.control.dataset.workspaceSelectReady === 'true') return;
            const values = Array.from(select.selectedOptions, option => option.value);
            const label = select.closest('.field')?.querySelector('label')?.textContent.trim() || select.getAttribute('aria-label') || select.name.replaceAll('_', ' ');
            const placeholder = Array.from(select.options).find(option => option.value === '')?.textContent.trim() || current?.settings.placeholder || '';
            const config = { closeAfterSelect: true, maxItems: select.multiple ? null : 1, maxOptions: null, ...current?.settings, placeholder, controlInput: null, searchField: [], create: false, dropdownParent: null };
            current?.destroy();
            try {
                const replacement = new window.TomSelect(select, config);
                replacement.setValue(select.multiple ? values : (values[0] || ''), true);
                replacement.control.setAttribute('aria-label', label);
                replacement.control.dataset.workspaceSelectReady = 'true';
                replacement.control_input?.setAttribute('aria-label', label);
                // With controlInput:null the normal input placeholder is not
                // visible. Render its original empty-option label explicitly.
                if (placeholder) {
                    const text = element('span', 'workspace-select-placeholder', placeholder);
                    text.style.color = '#71829a';
                    text.setAttribute('aria-hidden', 'true');
                    replacement.control.append(text);
                    const syncPlaceholder = () => {
                        const value = replacement.getValue();
                        text.style.display = (Array.isArray(value) ? value.length : value) ? 'none' : 'inline';
                    };
                    ['change', 'item_add', 'item_remove', 'clear'].forEach(name => replacement.on(name, syncPlaceholder));
                    syncPlaceholder();
                }
            } catch (_) {
                select.tomselect?.destroy();
                setValue(select, values);
            }
        });
        state.form.querySelectorAll('.schedule-quality-date-picker').forEach(input => {
            // A history snapshot contains native controls, not widget objects.
            // Rebuild the same available-date choice without a text input.
            if (!input._flatpickr && window.flatpickr) {
                const dates = (input.dataset.updatedDates || '').split(',').filter(Boolean);
                window.flatpickr(input, {
                    altInput: true, altFormat: 'j M Y', dateFormat: 'Y-m-d', disableMobile: true,
                    enable: dates.length ? dates : undefined,
                    onChange: () => input.dispatchEvent(new Event('change', { bubbles: true })),
                });
            }
            const instance = input._flatpickr;
            if (!instance || state.calendars.has(instance)) return;
            // Native dialogs make body-level widgets inert. Keep the calendar
            // in the same top layer, with viewport-bounded positioning.
            const calendar = instance.calendarContainer;
            const record = {
                instance, input, calendar,
                parent: calendar.parentNode,
                next: calendar.nextSibling,
                appendTo: instance.config.appendTo,
                style: calendar.getAttribute('style'),
            };
            record.onOpen = () => window.requestAnimationFrame(() => positionCalendars(state));
            instance.config.onOpen.push(record.onOpen);
            instance.config.appendTo = state.dialog;
            state.dialog.append(calendar);
            if (instance.altInput) instance.altInput.readOnly = true;
            state.calendars.set(instance, record);
        });
    };
    const open = state => {
        if (state.submitting || !state.dialog.isConnected) return;
        prepareWidgets(state);
        restore(state);
        state.dialog.returnValue = '';
        state.dialog.showModal();
        state.toggle.setAttribute('aria-expanded', 'true');
        state.closeButton.focus();
    };
    const resetDraft = state => {
        state.restoring = true;
        const defaults = { project_status: 'Client Submitted', project_state: 'Live', exclude_blanks: '1' };
        fields(state.form).forEach(control => {
            // Source choice is context, not a report filter to reset.
            if (control.name === 'report_source' || control.name === 'csrfmiddlewaretoken') return;
            if (control.tagName === 'SELECT') {
                const requested = state.clearQuery.get(control.name) ?? defaults[control.name] ?? '';
                const value = Array.from(control.options).some(option => option.value === requested)
                    ? requested : (control.options[0]?.value || '');
                setValue(control, value);
            } else if (control._flatpickr || control.matches('.schedule-quality-date-picker')) setValue(control, '');
            else if (control.type !== 'hidden') {
                if (['checkbox', 'radio'].includes(control.type)) control.checked = control.defaultChecked;
                else control.value = '';
            }
        });
        state.restoring = false;
    };
    const apply = state => {
        if (state.submitting || !state.form.reportValidity()) return;
        state.allowSubmit = true;
        state.form.requestSubmit();
        state.allowSubmit = false;
    };
    const teardown = state => {
        close(state, 'teardown');
        const { form, triggerAttribute, triggerValue } = state;
        form.removeAttribute('hx-trigger');
        form.removeAttribute('data-hx-trigger');
        if (triggerValue !== null) form.setAttribute(triggerAttribute, triggerValue);
        for (const record of state.calendars.values()) {
            if (record.input._flatpickr !== record.instance) continue;
            record.instance.config.onOpen = record.instance.config.onOpen.filter(hook => hook !== record.onOpen);
            record.instance.config.appendTo = record.appendTo;
            if (record.parent?.isConnected) record.parent.insertBefore(record.calendar, record.next?.parentNode === record.parent ? record.next : null);
            if (record.style === null) record.calendar.removeAttribute('style');
            else record.calendar.setAttribute('style', record.style);
        }
        if (state.placeholder.parentNode) state.placeholder.replaceWith(state.panel);
        state.originalHeading?.removeAttribute('hidden');
        state.actions.forEach(([node, style]) => {
            if (style === null) node.removeAttribute('style'); else node.setAttribute('style', style);
        });
        state.toolbar.remove();
        state.dialog.remove();
        state.layout.classList.remove('workspace-filters-enhanced');
        state.emptySidebar?.classList.remove('workspace-filter-origin');
        state.listeners.forEach(([node, name, handler, capture]) => node.removeEventListener(name, handler, capture));
        enhanced.delete(state.panel);
        states.delete(state);
    };

    const enhance = panel => {
        if (enhanced.has(panel)) { prepareWidgets(enhanced.get(panel)); return; }
        const form = panel.querySelector('form');
        const layout = panel.closest('.overview-layout, .validation-layout, .portfolio-layout');
        if (!form || !layout || panel.closest('dialog')) return;
        const id = `workspace-report-filters-${++serial}`;
        const dialog = element('dialog', 'workspace-filter-dialog');
        dialog.id = id;
        dialog.setAttribute('aria-labelledby', `${id}-title`);
        const heading = element('div', 'workspace-filter-header');
        const title = element('h2', 'workspace-filter-title', 'Filters');
        title.id = `${id}-title`;
        const headingActions = element('div', 'workspace-filter-heading-actions');
        const resetButton = button('workspace-filter-reset workspace-icon-button', 'Reset filters', 'arrow-counter-clockwise');
        const closeButton = button('workspace-filter-close workspace-icon-button', 'Close filters', 'x');
        headingActions.append(resetButton, closeButton);
        heading.append(title, headingActions);
        const body = element('div', 'workspace-filter-body');
        const status = element('p', 'workspace-filter-status');
        status.setAttribute('role', 'status');
        status.hidden = true;
        const footer = element('div', 'workspace-filter-footer');
        const cancelButton = button('workspace-filter-cancel', 'Cancel');
        const applyButton = button('workspace-filter-apply', 'Apply filters');
        footer.append(cancelButton, applyButton);
        dialog.append(heading, body, status, footer);
        const toolbar = element('div', 'workspace-filter-toolbar');
        const toggle = button('workspace-filter-toggle', 'Filters');
        toggle.setAttribute('aria-controls', id);
        toggle.setAttribute('aria-haspopup', 'dialog');
        toggle.setAttribute('aria-expanded', 'false');
        toolbar.append(toggle);

        const triggerAttribute = form.hasAttribute('data-hx-trigger') ? 'data-hx-trigger' : 'hx-trigger';
        const clearLink = form.querySelector('.overview-filter-actions a, .validation-filter-actions a, .portfolio-filter-actions a');
        const state = {
            panel, form, layout, dialog, toolbar, toggle, closeButton, applyButton, status,
            triggerAttribute, triggerValue: form.getAttribute(triggerAttribute),
            placeholder: document.createComment('Report filters'),
            originalHeading: panel.querySelector('h2'),
            emptySidebar: panel.closest('.portfolio-sidebar'),
            clearQuery: new URL(clearLink?.href || location.href).searchParams,
            actions: Array.from(panel.querySelectorAll('.overview-filter-actions, .validation-filter-actions, .portfolio-filter-actions'), node => [node, node.getAttribute('style')]),
            calendars: new Map(), listeners: [], applied: [], restoring: false, submitting: false, allowSubmit: false,
        };
        const listen = (node, name, handler, capture = false) => {
            node.addEventListener(name, handler, capture);
            state.listeners.push([node, name, handler, capture]);
        };
        enhanced.set(panel, state);
        states.add(state);
        panel.before(state.placeholder);
        body.append(panel);
        layout.prepend(toolbar);
        layout.append(dialog);
        layout.classList.add('workspace-filters-enhanced');
        state.emptySidebar?.classList.add('workspace-filter-origin');
        if (state.originalHeading) state.originalHeading.hidden = true;
        state.actions.forEach(([node]) => { node.style.display = 'none'; });
        // Preserve a selected reporting source even in templates without the
        // source-aware hidden field used by the production reporting module.
        if (!form.elements.namedItem('report_source')) {
            const source = new URLSearchParams(location.search).get('report_source') || document.getElementById('report-source')?.value;
            if (['combined', 'p6_22', 'p6_23'].includes(source)) {
                const hidden = document.createElement('input');
                hidden.type = 'hidden'; hidden.name = 'report_source'; hidden.value = source;
                form.append(hidden);
            }
        }
        form.removeAttribute('hx-trigger');
        form.removeAttribute('data-hx-trigger');
        listen(toggle, 'click', () => open(state));
        listen(resetButton, 'click', () => resetDraft(state));
        listen(closeButton, 'click', () => close(state));
        listen(cancelButton, 'click', () => close(state));
        listen(applyButton, 'click', () => apply(state));
        listen(dialog, 'cancel', event => { event.preventDefault(); close(state); });
        listen(dialog, 'click', event => {
            if (event.target !== dialog) return;
            const box = dialog.getBoundingClientRect();
            if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close(state);
        });
        listen(dialog, 'close', () => {
            toggle.setAttribute('aria-expanded', 'false');
            if (dialog.returnValue !== 'teardown' && toggle.isConnected) toggle.focus();
        });
        listen(dialog, 'scroll', () => positionCalendars(state), true);
        listen(panel, 'click', event => {
            if (!event.target.closest('.overview-filter-actions, .validation-filter-actions, .portfolio-filter-actions')) return;
            event.preventDefault(); event.stopImmediatePropagation(); resetDraft(state);
        }, true);
        const draftChanged = event => {
            // Inline portfolio→project dependencies run before this bubble
            // handler. Draft edits must not update report links or send GETs.
            event.stopImmediatePropagation();
            if (!state.restoring && !dialog.open && event.target.name === 'check') apply(state);
        };
        listen(form, 'change', draftChanged);
        listen(form, 'input', event => event.stopImmediatePropagation());
        listen(form, 'submit', event => {
            if (!state.allowSubmit) { event.preventDefault(); event.stopImmediatePropagation(); return; }
            state.previousApplied = state.applied;
            state.applied = snapshot(form);
            state.submitting = true;
            state.applyButton.disabled = true;
            state.status.hidden = true;
            close(state, 'apply');
        }, true);
        listen(form, 'htmx:beforeRequest', event => { if (!state.submitting) event.preventDefault(); }, true);
        listen(form, 'htmx:afterRequest', event => {
            state.submitting = false;
            state.applyButton.disabled = false;
            if ((event.detail.failed || event.detail.successful === false) && dialog.isConnected) {
                state.applied = state.previousApplied || state.applied;
                restore(state);
                state.status.textContent = 'The filters could not be applied. Please try again.';
                state.status.hidden = false;
                open(state);
            }
            state.previousApplied = null;
        });
        // HTMX 2 reprocesses the trigger binding without destroying widgets.
        // GET, target, select, swap and history attributes remain untouched.
        window.htmx?.process(form);
        prepareWidgets(state);
        state.applied = snapshot(form);
    };
    const initialize = (root = document) => {
        if (root.matches?.(panelSelector)) enhance(root);
        root.querySelectorAll?.(panelSelector).forEach(enhance);
        for (const state of states) {
            if (!state.layout.isConnected) teardown(state);
            else prepareWidgets(state);
        }
        synchronizeSidebar();
    };
    document.addEventListener('htmx:load', event => window.queueMicrotask(() => initialize(event.detail.elt)));
    document.addEventListener('htmx:afterSwap', event => window.queueMicrotask(() => initialize(event.target)));
    document.addEventListener('htmx:afterSettle', event => {
        if (!event.detail.xhr || event.detail.xhr.status < 400) window.queueMicrotask(synchronizeSidebar);
    });
    document.addEventListener('htmx:beforeCleanupElement', event => {
        const removed = event.detail.elt;
        for (const state of states) {
            if (removed === state.panel || removed === state.layout || removed.contains?.(state.dialog)) teardown(state);
        }
    });
    document.addEventListener('htmx:beforeHistorySave', () => {
        // Cache native forms, never generated dialogs or widget markup. The
        // current URL still describes the previous applied filters at this
        // point; a validation partial may keep its form after the save.
        const retained = [];
        for (const state of Array.from(states)) {
            retained.push({ form: state.form, values: snapshot(state.form) });
            state.applied = state.previousApplied || state.applied;
            restore(state);
            teardown(state);
            state.form.querySelectorAll('select').forEach(select => select.tomselect?.destroy());
            state.form.querySelectorAll('.schedule-quality-date-picker').forEach(input => input._flatpickr?.destroy());
            restore(state);
            fields(state.form).forEach(control => {
                if (control.tagName === 'SELECT') Array.from(control.options).forEach(option => option.toggleAttribute('selected', option.selected));
                else if (['checkbox', 'radio'].includes(control.type)) control.toggleAttribute('checked', control.checked);
                else control.setAttribute('value', control.value);
            });
        }
        window.queueMicrotask(() => {
            for (const { form, values } of retained) {
                if (form.isConnected) restore({ form, applied: values });
            }
            initialize();
        });
    });
    document.addEventListener('htmx:historyRestore', () => window.queueMicrotask(() => initialize()));
    window.addEventListener('resize', () => { for (const state of states) positionCalendars(state); });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initialize());
    else initialize();
})();
