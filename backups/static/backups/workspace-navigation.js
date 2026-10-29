(() => {
    'use strict';
    const rail = document.querySelector('[data-workspace-navigation]');
    if (!rail) return;
    const triggers = [...rail.querySelectorAll('[data-workspace-menu-trigger]')];
    const flyouts = [...document.querySelectorAll('[data-workspace-flyout]')];
    const accountTrigger = rail.querySelector('[data-workspace-account]');
    const accountMenu = document.getElementById('workspace-account-menu');
    const sidebar = document.getElementById('workspace-sidebar');
    const main = document.getElementById('workspace-main');
    const sidebarToggle = document.querySelector('[data-workspace-sidebar-toggle]');
    const mobile = window.matchMedia('(max-width: 849px)');
    let openTimer, closeTimer, focusFrame, currentTrigger = null, currentFlyout = null;

    const clearTimers = () => { window.clearTimeout(openTimer); window.clearTimeout(closeTimer); };
    const closeFlyout = (restoreFocus = false) => {
        clearTimers(); window.cancelAnimationFrame(focusFrame);
        const trigger = currentTrigger;
        if (currentFlyout) currentFlyout.hidden = true;
        if (trigger) { trigger.setAttribute('aria-expanded', 'false'); trigger.classList.remove('is-open'); }
        currentFlyout = null; currentTrigger = null;
        if (restoreFocus) trigger?.focus();
    };
    const closeAccount = (restoreFocus = false) => {
        if (!accountMenu || accountMenu.hidden) return;
        accountMenu.hidden = true; accountTrigger?.setAttribute('aria-expanded', 'false');
        if (restoreFocus) accountTrigger?.focus();
    };
    const positionFlyout = () => {
        if (!currentFlyout || !currentTrigger) return;
        const box = currentTrigger.getBoundingClientRect();
        currentFlyout.style.maxHeight = `${Math.max(36, Math.min(320, window.innerHeight - 16))}px`;
        currentFlyout.style.left = `${Math.max(8, Math.min(rail.getBoundingClientRect().right - 4, document.documentElement.clientWidth - 236))}px`;
        currentFlyout.style.top = `${Math.max(8, Math.min(box.top - 2, window.innerHeight - currentFlyout.getBoundingClientRect().height - 8))}px`;
    };
    const openFlyout = (trigger, focusFirst = false) => {
        clearTimers(); closeAccount();
        const flyout = document.getElementById(`workspace-flyout-${trigger.dataset.workspaceMenuTrigger}`);
        if (!flyout) return;
        if (currentFlyout !== flyout) closeFlyout();
        currentTrigger = trigger; currentFlyout = flyout;
        flyout.hidden = false; trigger.setAttribute('aria-expanded', 'true'); trigger.classList.add('is-open');
        positionFlyout();
        if (focusFirst) focusFrame = window.requestAnimationFrame(() => flyout.querySelector('[role="menuitem"]')?.focus());
    };
    const beginClose = () => {
        clearTimers();
        closeTimer = window.setTimeout(() => { if (!currentFlyout?.contains(document.activeElement)) closeFlyout(); }, 220);
    };
    const setSidebarOpen = (open, restoreFocus = false) => {
        if (!sidebar) return;
        document.body.classList.toggle('workspace-sidebar-open', open && mobile.matches);
        sidebar.inert = mobile.matches && !open;
        if (main) main.inert = mobile.matches && open;
        sidebarToggle?.setAttribute('aria-expanded', String(open && mobile.matches));
        sidebarToggle?.setAttribute('aria-label', open && mobile.matches ? 'Close navigation' : 'Expand navigation');
        if (open && mobile.matches) sidebar.querySelector('a,summary')?.focus();
        if (restoreFocus) sidebarToggle?.focus();
    };

    for (const trigger of triggers) {
        const item = trigger.closest('.workspace-rail-item');
        item.addEventListener('mouseenter', () => { clearTimers(); openTimer = window.setTimeout(() => openFlyout(trigger), 350); });
        item.addEventListener('mouseleave', beginClose);
        trigger.addEventListener('click', () => closeFlyout());
        trigger.addEventListener('keydown', event => {
            if (['ArrowRight', 'ArrowDown'].includes(event.key)) { event.preventDefault(); openFlyout(trigger, true); }
        });
    }
    rail.querySelector('.workspace-rail-link:not([data-workspace-menu-trigger])')?.addEventListener('mouseenter', () => closeFlyout());
    for (const flyout of flyouts) {
        flyout.addEventListener('mouseenter', clearTimers);
        flyout.addEventListener('mouseleave', beginClose);
        flyout.addEventListener('focusin', clearTimers);
        flyout.addEventListener('click', event => { if (event.target.closest('a')) closeFlyout(); });
        flyout.addEventListener('keydown', event => {
            if (['Escape', 'ArrowLeft'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); closeFlyout(true); return; }
            if (event.key === 'Tab') { closeFlyout(true); return; }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            const items = [...flyout.querySelectorAll('[role="menuitem"]')];
            const index = items.indexOf(document.activeElement);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
            event.preventDefault(); items[next]?.focus();
        });
    }
    accountTrigger?.addEventListener('click', () => {
        const open = accountMenu.hidden;
        closeFlyout(); accountMenu.hidden = !open; accountTrigger.setAttribute('aria-expanded', String(open));
    });
    sidebarToggle?.addEventListener('click', () => setSidebarOpen(!document.body.classList.contains('workspace-sidebar-open')));
    document.querySelector('[data-workspace-sidebar-close]')?.addEventListener('click', () => setSidebarOpen(false, true));
    const outside = event => {
        if (currentFlyout && !currentFlyout.contains(event.target) && !currentTrigger?.contains(event.target)) closeFlyout();
        if (accountMenu && !accountMenu.hidden && !accountMenu.contains(event.target) && !accountTrigger?.contains(event.target)) closeAccount();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (currentFlyout) { event.preventDefault(); closeFlyout(true); }
        else if (accountMenu && !accountMenu.hidden) { event.preventDefault(); closeAccount(true); }
        else if (document.body.classList.contains('workspace-sidebar-open')) setSidebarOpen(false, true);
    });
    window.addEventListener('resize', positionFlyout);
    mobile.addEventListener('change', () => { closeFlyout(); setSidebarOpen(false); });
    window.addEventListener('pagehide', () => { closeFlyout(); closeAccount(); clearTimers(); });
    setSidebarOpen(false);
})();
