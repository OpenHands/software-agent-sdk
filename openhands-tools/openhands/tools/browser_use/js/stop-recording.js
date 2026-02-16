(function() {
    var events = window.__rrweb_events || [];

    // Stop the recording if active
    if (window.__rrweb_stopFn) {
        window.__rrweb_stopFn();
        window.__rrweb_stopFn = null;
    }

    // Clear flags
    window.__rrweb_should_record = false;
    window.__rrweb_events = [];

    return JSON.stringify({events: events});
})();
