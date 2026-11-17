/*
 * Shared expand / collapse for group cards.
 *
 * ONE implementation, as with filters.js. The reports page, the position
 * detail page and L1's usage board all show the same thing — a card with a
 * header you can fold away — and three private copies of that behaviour is
 * how the filter bar drifted before it was centralised.
 *
 * ── Markup contract ──────────────────────────────────────────────────────
 *
 *   <div class="group-card" data-collapse-scope="reports">
 *       <div class="group-card-head">
 *           ...
 *           <button class="cycle-toggle-btn" data-collapse-toggle>
 *               <span class="toggle-chevron">&#9660;</span>
 *           </button>
 *       </div>
 *       <div class="card-body-wrap" data-collapse-body data-collapse-key="POS-1">
 *           <div class="card-body-inner"> ... </div>
 *       </div>
 *   </div>
 *
 *   <button data-collapse-all="reports">Collapse All</button>
 *   <button data-expand-all="reports">Expand All</button>
 *
 * data-collapse-key makes the open/closed choice stick across visits, per
 * scope, in localStorage. It is a display preference and nothing else — no
 * identity, no scores. A card with no key simply does not persist.
 *
 * filters.js opens a collapsed card that matches an active filter (see its
 * setExpanded) and restores the user's own choice when the filters clear, so
 * the two components deliberately share the `.collapsed` class and the
 * `.cycle-toggle-btn` selector. Do not rename either in isolation.
 */
(function () {
    'use strict';

    function storageKey(body) {
        var card = body.closest('[data-collapse-scope]');
        var scope = card ? card.dataset.collapseScope : '';
        var key = body.dataset.collapseKey;
        return (scope && key) ? 'lt-collapse-' + scope + '-' + key : null;
    }

    function buttonFor(body) {
        var card = body.closest('.group-card') || body.parentNode;
        return card ? card.querySelector('[data-collapse-toggle]') : null;
    }

    function set(body, collapsed, remember) {
        body.classList.toggle('collapsed', collapsed);
        var btn = buttonFor(body);
        if (btn) {
            btn.classList.toggle('collapsed', collapsed);
            btn.title = collapsed ? 'Expand' : 'Collapse';
            btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }
        if (!remember) { return; }
        var key = storageKey(body);
        if (!key) { return; }
        try { localStorage.setItem(key, collapsed ? 'closed' : 'open'); } catch (e) {}
    }

    function bodiesIn(scope) {
        var sel = scope
            ? '[data-collapse-scope="' + scope + '"] [data-collapse-body]'
            : '[data-collapse-body]';
        return Array.prototype.slice.call(document.querySelectorAll(sel));
    }

    /*
     * TABLE-ROW PANELS. A button in a <tr> with data-row-toggle opens the
     * next sibling <tr data-row-panel> (hidden by default — unlike cards,
     * a manage panel is closed until asked for). One open at a time per
     * table. data-open-label / data-closed-label swap the button text.
     */
    function setRow(btn, panel, open) {
        panel.hidden = !open;
        btn.closest('tr').classList.toggle('row-open', open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.classList.toggle('btn-primary', open);
        btn.classList.toggle('btn-secondary', !open);
        if (btn.dataset.openLabel) {
            btn.textContent = open ? btn.dataset.openLabel : btn.dataset.closedLabel;
        }
    }

    document.addEventListener('click', function (event) {
        var rowBtn = event.target.closest('[data-row-toggle]');
        if (rowBtn) {
            var row = rowBtn.closest('tr');
            var panel = row && row.nextElementSibling;
            if (!panel || !panel.hasAttribute('data-row-panel')) { return; }
            var opening = panel.hidden;
            var table = row.closest('table');
            if (opening && table) {
                table.querySelectorAll('[data-row-toggle]').forEach(function (b) {
                    var p = b.closest('tr').nextElementSibling;
                    if (b !== rowBtn && p && !p.hidden) { setRow(b, p, false); }
                });
            }
            setRow(rowBtn, panel, opening);
            return;
        }

        var toggle = event.target.closest('[data-collapse-toggle]');
        if (toggle) {
            var card = toggle.closest('.group-card') || toggle.parentNode;
            var body = card && card.querySelector('[data-collapse-body]');
            if (body) { set(body, !body.classList.contains('collapsed'), true); }
            return;
        }

        var all = event.target.closest('[data-collapse-all], [data-expand-all]');
        if (!all) { return; }
        var collapse = all.hasAttribute('data-collapse-all');
        var scope = collapse ? all.dataset.collapseAll : all.dataset.expandAll;
        bodiesIn(scope).forEach(function (body) { set(body, collapse, true); });
    });

    function init() {
        bodiesIn(null).forEach(function (body) {
            var key = storageKey(body);
            var stored = null;
            try { stored = key && localStorage.getItem(key); } catch (e) {}
            // No stored choice means open: a card that hides its contents on
            // first sight makes the reader click to find out there is nothing
            // in it.
            set(body, stored === 'closed', false);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
