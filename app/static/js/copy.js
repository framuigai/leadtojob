/*
 * Shared "copy to clipboard" for the survey link.
 *
 * ONE implementation, as with filters.js and collapse.js. The CSS for this
 * already existed — `.link-copy button.copied` has been in main.css since the
 * deployment screen was written — but nothing ever wired it up, so the button
 * was never rendered and the link had to be selected by hand.
 *
 * ── Markup contract ──────────────────────────────────────────────────────
 *
 *   <div class="link-copy">
 *       <input type="text" readonly value="https://...">
 *       <button type="button" data-copy>Copy</button>
 *   </div>
 *
 * The button copies the value of the input inside the SAME `.link-copy`
 * wrapper. No id plumbing, so a page can render N of them — one per
 * deployment — without the ids colliding, which is exactly what happened to
 * the first version of the merged cycle page.
 *
 * ── Why it does not rely on navigator.clipboard alone ────────────────────
 *
 * `navigator.clipboard` is undefined on an insecure origin, and this app is
 * served over plain http on localhost during development. Falling back to
 * `document.execCommand('copy')` keeps the button working there instead of
 * silently doing nothing, which reads as a broken button rather than as a
 * browser restriction. The input is already in the DOM and already readonly,
 * so the fallback needs no hidden element.
 */
(function () {
    'use strict';

    var RESET_MS = 1600;

    function flash(button, text, ok) {
        var original = button.getAttribute('data-copy-label') || 'Copy';
        button.textContent = text;
        if (ok) { button.classList.add('copied'); }
        window.setTimeout(function () {
            button.textContent = original;
            button.classList.remove('copied');
        }, RESET_MS);
    }

    function legacyCopy(input) {
        // Returns true when the copy actually happened. execCommand reports
        // failure rather than throwing in most browsers, so check the result.
        try {
            input.focus();
            input.setSelectionRange(0, input.value.length);
            return document.execCommand('copy');
        } catch (e) {
            return false;
        }
    }

    function handle(button) {
        var wrap = button.closest('.link-copy');
        var input = wrap && wrap.querySelector('input');
        if (!input) { return; }

        // Select either way: on success it shows what was taken, and on
        // failure it leaves the text ready for a manual ctrl-C.
        input.select();

        function fallback() {
            var ok = legacyCopy(input);
            flash(button, ok ? 'Copied' : 'Press Ctrl+C', ok);
        }

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(input.value).then(function () {
                flash(button, 'Copied', true);
            }, fallback);
            return;
        }

        fallback();
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-copy]');
        if (button) {
            event.preventDefault();
            handle(button);
        }
    });
}());
