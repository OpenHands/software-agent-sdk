(function() {
    // If Promise doesn't exist, scripts weren't injected yet
    if (!window.__rrweb_ready_promise) {
        return Promise.resolve({success: false, error: 'not_injected'});
    }
    // If already loaded, return immediately
    if (window.__rrweb_ready) {
        return Promise.resolve({success: true});
    }
    // If already failed, return immediately
    if (window.__rrweb_load_failed) {
        return Promise.resolve({success: false, error: 'load_failed'});
    }
    // Wait for the Promise to resolve
    return window.__rrweb_ready_promise;
})();
