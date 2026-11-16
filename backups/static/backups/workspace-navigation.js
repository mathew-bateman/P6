(() => {
    'use strict';
    const rail = document.querySelector('[data-workspace-navigation]');
    if (!rail) return;
    const accountTrigger = rail.querySelector('[data-workspace-account]');
    const accountMenu = document.getElementById('workspace-account-menu');

    const closeAccount = (restoreFocus = false) => {
        if (!accountMenu || accountMenu.hidden) return;
        accountMenu.hidden = true; accountTrigger?.setAttribute('aria-expanded', 'false');
        if (restoreFocus) accountTrigger?.focus();
    };
    // Clear the obsolete marker if a previously loaded page links to it.
    if (window.location.hash === '#workspace-open') {
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
    }

    accountTrigger?.addEventListener('click', () => {
        const open = accountMenu.hidden;
        accountMenu.hidden = !open; accountTrigger.setAttribute('aria-expanded', String(open));
        if (open) accountMenu.querySelector('[data-workspace-signout]')?.focus();
    });
    accountMenu?.querySelector('[data-workspace-account-close]')?.addEventListener('click', () => closeAccount(true));
    const signoutDialog = document.getElementById('workspace-signout-dialog');
    accountMenu?.querySelector('[data-workspace-signout]')?.addEventListener('click', () => {
        closeAccount();
        signoutDialog.showModal();
    });
    signoutDialog?.querySelector('[data-workspace-signout-cancel]')?.addEventListener('click', () => signoutDialog.close());
    signoutDialog?.addEventListener('close', () => accountTrigger?.focus());
    signoutDialog?.addEventListener('click', event => {
        if (event.target !== signoutDialog) return;
        const bounds = signoutDialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) signoutDialog.close();
    });
    const outside = event => {
        if (accountMenu && !accountMenu.hidden && !accountMenu.contains(event.target) && !accountTrigger?.contains(event.target)) closeAccount();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (accountMenu && !accountMenu.hidden) { event.preventDefault(); closeAccount(true); }
    });
    window.addEventListener('pagehide', () => closeAccount());
})();
