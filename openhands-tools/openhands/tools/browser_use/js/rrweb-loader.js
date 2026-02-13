(function() {
    if (window.__rrweb_loaded) return;
    window.__rrweb_loaded = true;

    // Initialize storage for events (per-page, will be flushed to backend)
    window.__rrweb_events = window.__rrweb_events || [];
    // Flag to indicate if recording should auto-start on new pages (cross-page)
    // This is ONLY set after explicit start_recording call, not on initial load
    window.__rrweb_should_record = window.__rrweb_should_record || false;
    // Flag to track if rrweb failed to load
    window.__rrweb_load_failed = false;

    // Create a Promise that resolves when rrweb loads (event-driven waiting)
    var resolveReady;
    window.__rrweb_ready_promise = new Promise(function(resolve) {
        resolveReady = resolve;
    });

    function loadRrweb() {
        var s = document.createElement('script');
        s.src = '{{CDN_URL}}';
        s.onload = function() {
            window.__rrweb_ready = true;
            console.log('[rrweb] Loaded successfully from CDN');
            resolveReady({success: true});
            // Auto-start recording ONLY if flag is set (for cross-page continuity)
            // This flag is only true after an explicit start_recording call
            if (window.__rrweb_should_record && !window.__rrweb_stopFn) {
                window.startRecordingInternal();
            }
        };
        s.onerror = function() {
            console.error('[rrweb] Failed to load from CDN');
            window.__rrweb_load_failed = true;
            resolveReady({success: false, error: 'load_failed'});
        };
        (document.head || document.documentElement).appendChild(s);
    }

    // Internal function to start recording (used for auto-start on navigation)
    window.startRecordingInternal = function() {
        var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                       (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
        if (!recordFn || window.__rrweb_stopFn) return;

        window.__rrweb_events = [];
        window.__rrweb_stopFn = recordFn({
            emit: function(event) {
                window.__rrweb_events.push(event);
            }
        });
        console.log('[rrweb] Auto-started recording on new page');
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadRrweb);
    } else {
        loadRrweb();
    }
})();
