(function() {
    if (window.__rrweb_stopFn) return {status: 'already_recording'};
    // Check if rrweb failed to load from CDN
    if (window.__rrweb_load_failed) return {status: 'load_failed'};
    // rrweb UMD module exports to window.rrweb (not rrwebRecord)
    var recordFn = (typeof rrweb !== 'undefined' && rrweb.record) ||
                   (typeof rrwebRecord !== 'undefined' && rrwebRecord.record);
    if (!recordFn) return {status: 'not_loaded'};
    window.__rrweb_events = [];
    window.__rrweb_should_record = true;
    window.__rrweb_stopFn = recordFn({
        emit: function(event) {
            window.__rrweb_events.push(event);
        }
    });
    return {status: 'started'};
})();
