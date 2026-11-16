'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup({ mobile = true, hash = '', sidebarPresent = true } = {}) {
    const windowListeners = {}, documentListeners = {};
    let sidebarOpen = false;
    const document = {
        activeElement: null,
        body: { classList: {
            toggle(name, value) { if (name === 'workspace-sidebar-open') sidebarOpen = value; },
            contains(name) { return name === 'workspace-sidebar-open' && sidebarOpen; },
        } },
        addEventListener(name, fn) { (documentListeners[name] ??= []).push(fn); },
    };
    function element() {
        return {
            listeners: {}, attrs: {}, hidden: false, inert: false,
            addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); },
            dispatch(name, props = {}) {
                const event = { target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...props };
                for (const fn of this.listeners[name] ?? []) fn(event);
                return event;
            },
            setAttribute(name, value) { this.attrs[name] = value; },
            contains(target) { return target === this; },
            focus() { document.activeElement = this; },
        };
    }
    const rail = element(), account = element(), accountMenu = element();
    const sidebar = element(), sidebarLink = element(), main = element(), toggle = element(), scrim = element();
    accountMenu.hidden = true;
    rail.querySelector = selector => selector === '[data-workspace-account]' ? account : null;
    sidebar.querySelector = () => sidebarLink;
    document.querySelector = selector => ({
        '[data-workspace-navigation]': rail,
        '[data-workspace-sidebar-toggle]': toggle,
        '[data-workspace-sidebar-close]': scrim,
    })[selector] ?? null;
    document.getElementById = id => ({
        'workspace-account-menu': accountMenu,
        'workspace-sidebar': sidebarPresent ? sidebar : null,
        'workspace-main': main,
    })[id] ?? null;
    const location = { pathname: '/schedule-quality/', search: '?report_source=p6_22', hash };
    const window = {
        location,
        history: { state: null, replaceState(state, title, url) { this.lastUrl = url; location.hash = ''; } },
        matchMedia: () => ({ matches: mobile, addEventListener() {} }),
        addEventListener(name, fn) { (windowListeners[name] ??= []).push(fn); },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/backups/workspace-navigation.js'), 'utf8'), { document, window });
    return { document, documentListeners, window, windowListeners, rail, account, accountMenu, sidebar, sidebarLink, main, toggle, scrim, isOpen: () => sidebarOpen };
}

test('legacy sidebar marker is removed without changing the query or blocking the page', () => {
    const ui = setup({ hash: '#workspace-open' });
    assert.equal(ui.window.history.lastUrl, '/schedule-quality/?report_source=p6_22');
    assert.equal(ui.sidebar.inert, false);
    assert.equal(ui.main.inert, false);
    assert.equal(ui.isOpen(), false);
});

test('ordinary navigation keeps the sidebar and content interactive', () => {
    for (const mobile of [true, false]) {
        const ui = setup({ mobile });
        assert.equal(ui.sidebar.inert, false);
        assert.equal(ui.main.inert, false);
        assert.equal(ui.window.history.lastUrl, undefined);
    }
});

test('account menu opens and Escape closes it and restores focus', () => {
    const ui = setup();
    ui.account.dispatch('click');
    assert.equal(ui.accountMenu.hidden, false);
    assert.equal(ui.account.attrs['aria-expanded'], 'true');
    for (const fn of ui.documentListeners.keydown ?? []) fn({ key: 'Escape', preventDefault() {} });
    assert.equal(ui.accountMenu.hidden, true);
    assert.equal(ui.account.attrs['aria-expanded'], 'false');
    assert.equal(ui.document.activeElement, ui.account);
});
