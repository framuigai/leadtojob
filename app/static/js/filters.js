/*
 * Shared filter / sort component.
 *
 * ONE implementation for every filterable list in the app — the admin user
 * list, the org status board, the domain coverage matrix, the L2/L3 reports
 * page and L1's org drill-down. Three hand-rolled copies had already started
 * to appear; a fourth and fifth would have meant the same control labels
 * behaving differently depending on which tier you were logged in as.
 *
 * Filtering is DISPLAY ONLY. It runs over rows the server has already scoped
 * and can never reveal a row the user was not sent. Branch isolation is
 * enforced in the route (see CLAUDE.md) — never move a filter server-side by
 * building a fresh query from a filter parameter.
 *
 * ── Markup contract ──────────────────────────────────────────────────────
 *
 *   <div class="filter-bar" data-filter-for="TARGET_ID">
 *       <div class="filter-field">
 *           <label for="x">Status</label>
 *           <select id="x" data-key="status">…</select>   ← data-key = row attr
 *       </div>
 *       <div class="filter-field">
 *           <label for="q">Search</label>
 *           <input id="q" data-key="name" data-match="search">
 *       </div>
 *       <select id="s" class="filter-sort" data-sort-for="TARGET_ID">
 *           <option value="created:-1" data-type="number">Newest first</option>
 *       </select>
 *       <button type="button" class="filter-clear">Clear</button>
 *   </div>
 *
 *   TARGET_ID is either a <table> (items = tbody rows) or any container
 *   (items = descendants marked [data-filter-item]).
 *
 * ── Per-control attributes ───────────────────────────────────────────────
 *   data-key      the item's data-* attribute to test          (required)
 *   data-match    exact (default) | contains | search
 *                   contains → pipe-delimited list, |a|b|, so "sales" cannot
 *                              substring-match "sales_enablement"
 *                   search   → case-insensitive substring
 *   data-param    query-string name (defaults to the control's id)
 *
 * ── Per-item attributes ──────────────────────────────────────────────────
 *   data-<key>    the values matched against
 *   data-expand   selector of a collapsible body to open while filtering, so
 *                 a match inside a collapsed card is never invisible
 *
 * ── Optional companions (matched by data-*-for="TARGET_ID") ──────────────
 *   .filter-count    live "Showing 3 of 8 cycles" line
 *   .filter-empty    shown when nothing matches (a <tr> inside a table)
 */
(function () {
    'use strict';

    function itemsOf(target) {
        if (target.tagName === 'TABLE') {
            var body = target.tBodies[0];
            if (!body) { return []; }
            return Array.prototype.slice.call(body.rows).filter(function (r) {
                return !r.classList.contains('filter-empty');
            });
        }
        return Array.prototype.slice.call(target.querySelectorAll('[data-filter-item]'));
    }

    function matches(item, ctrl) {
        var val = (ctrl.value || '').trim();
        if (!val) { return true; }
        var field = item.dataset[ctrl.dataset.key] || '';
        var mode = ctrl.dataset.match || 'exact';
        if (mode === 'contains') { return field.indexOf('|' + val + '|') !== -1; }
        if (mode === 'search') {
            return field.toLowerCase().indexOf(val.toLowerCase()) !== -1;
        }
        return field === val;
    }

    // Collapsible bodies are opened while a filter is active and restored to
    // the user's own choice when the filters clear — filtering must not
    // silently discard the expand/collapse state someone set by hand.
    function setExpanded(item, expanded) {
        var sel = item.dataset.expand;
        if (!sel) { return; }
        var body = item.querySelector(sel) || document.querySelector(sel);
        if (!body) { return; }
        var btn = item.querySelector('.cycle-toggle-btn, .admin-toggle-btn');
        if (expanded === null) {                      // restore
            expanded = body.dataset.userCollapsed !== '1';
            delete body.dataset.userCollapsed;        // re-capture next time
        } else if (body.dataset.userCollapsed === undefined) {
            body.dataset.userCollapsed = body.classList.contains('collapsed') ? '1' : '0';
        }
        body.classList.toggle('collapsed', !expanded);
        if (btn) {
            btn.classList.toggle('collapsed', !expanded);
            btn.title = expanded ? 'Collapse' : 'Expand';
        }
    }

    function Filterable(bar) {
        var targetId = bar.dataset.filterFor;
        var target = document.getElementById(targetId);
        if (!target) { return; }

        var controls = Array.prototype.slice.call(bar.querySelectorAll('[data-key]'));
        var sortSel = bar.querySelector('.filter-sort');
        var countEl = document.querySelector('.filter-count[data-count-for="' + targetId + '"]');
        var emptyEl = document.querySelector('.filter-empty[data-empty-for="' + targetId + '"]');
        var noun = bar.dataset.noun || 'result';
        var nounPlural = bar.dataset.nounPlural || (noun + 's');

        function paramName(ctrl) { return ctrl.dataset.param || ctrl.id; }

        function anyActive() {
            return controls.some(function (c) { return (c.value || '').trim() !== ''; });
        }

        function syncUrl() {
            try {
                var url = new URL(window.location.href);
                controls.concat(sortSel ? [sortSel] : []).forEach(function (c) {
                    if (!c) { return; }
                    var name = paramName(c);
                    if (!name) { return; }
                    if ((c.value || '').trim()) { url.searchParams.set(name, c.value); }
                    else { url.searchParams.delete(name); }
                });
                window.history.replaceState({}, '', url);
            } catch (e) { /* URL unsupported — filtering still works */ }
        }

        function readUrl() {
            try {
                var params = new URL(window.location.href).searchParams;
                controls.concat(sortSel ? [sortSel] : []).forEach(function (c) {
                    if (!c) { return; }
                    var v = params.get(paramName(c));
                    if (v === null) { return; }
                    // Only accept a value the control actually offers, so a
                    // hand-edited URL cannot inject arbitrary text into a
                    // <select> or resurrect a filter for a value that is not
                    // on the page.
                    if (c.tagName === 'SELECT') {
                        var ok = Array.prototype.some.call(c.options, function (o) {
                            return o.value === v;
                        });
                        if (!ok) { return; }
                    }
                    c.value = v;
                });
            } catch (e) { /* no-op */ }
        }

        function sort() {
            if (!sortSel || !sortSel.value) { return; }
            var parts = sortSel.value.split(':');
            var key = parts[0];
            var dir = parseInt(parts[1], 10) || 1;
            var opt = sortSel.options[sortSel.selectedIndex];
            var numeric = opt && opt.dataset.type === 'number';
            reorder(key, numeric, dir);
        }

        function reorder(key, numeric, dir) {
            var parent = target.tagName === 'TABLE' ? target.tBodies[0] : target;
            var list = itemsOf(target);
            list.sort(function (a, b) {
                var av = a.dataset[key] || '';
                var bv = b.dataset[key] || '';
                if (numeric) { return ((parseFloat(av) || 0) - (parseFloat(bv) || 0)) * dir; }
                // Blanks sort last in either direction — a nameless row drifting
                // into the middle of an A-Z list reads as a bug.
                if (!av !== !bv) { return av ? -1 : 1; }
                return av.localeCompare(bv, undefined, {sensitivity: 'base'}) * dir;
            });
            list.forEach(function (el) { parent.appendChild(el); });
            if (emptyEl && emptyEl.parentNode === parent) { parent.appendChild(emptyEl); }
        }

        function apply() {
            var list = itemsOf(target);
            var active = anyActive();
            var shown = 0;

            list.forEach(function (item) {
                var ok = controls.every(function (c) { return matches(item, c); });
                item.style.display = ok ? '' : 'none';
                if (ok) { shown++; }
                if (item.dataset.expand) {
                    // Matched cards open while filtering; when the filters
                    // clear, every card goes back to the user's own choice.
                    // A hidden card is left alone — nobody can see it.
                    if (active && ok) { setExpanded(item, true); }
                    else if (!active) { setExpanded(item, null); }
                }
            });

            if (emptyEl) { emptyEl.style.display = (shown || !list.length) ? 'none' : ''; }
            if (countEl) {
                var word = list.length === 1 ? noun : nounPlural;
                countEl.textContent = shown === list.length
                    ? 'Showing all ' + list.length + ' ' + word
                    : 'Showing ' + shown + ' of ' + list.length + ' ' + nounPlural;
            }
            bar.classList.toggle('filter-bar-active', active);
            syncUrl();
            target.dispatchEvent(new CustomEvent('filters:applied', {
                bubbles: true, detail: {shown: shown, total: list.length, active: active},
            }));
        }

        // Click-to-sort table headers (tables only; card lists use the select).
        target.querySelectorAll('th.sortable').forEach(function (th) {
            th.addEventListener('click', function () {
                var asc = !th.classList.contains('sort-asc');
                target.querySelectorAll('th.sortable').forEach(function (o) {
                    o.classList.remove('sort-asc', 'sort-desc');
                });
                th.classList.add(asc ? 'sort-asc' : 'sort-desc');
                reorder(th.dataset.sort, th.dataset.type === 'number', asc ? 1 : -1);
            });
        });

        controls.forEach(function (c) {
            c.addEventListener(c.tagName === 'SELECT' ? 'change' : 'input', apply);
        });
        if (sortSel) {
            sortSel.addEventListener('change', function () { sort(); syncUrl(); });
        }
        var clearBtn = bar.querySelector('.filter-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                controls.forEach(function (c) { c.value = ''; });
                apply();
            });
        }

        readUrl();
        sort();
        apply();
    }

    function init() {
        document.querySelectorAll('.filter-bar[data-filter-for]').forEach(Filterable);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
