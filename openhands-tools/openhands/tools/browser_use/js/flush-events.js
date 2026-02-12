(function() {
    var events = window.__rrweb_events || [];
    // Clear browser-side events after flushing
    window.__rrweb_events = [];
    return JSON.stringify({events: events});
})();
