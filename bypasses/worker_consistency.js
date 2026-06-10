// Worker Context Consistency
// Patches Worker, SharedWorker, and ServiceWorker to inject navigator overrides
// so that cross-context validation (CreepJS, browserscan) sees consistent values.

(function() {
    const DEVICE_MEMORY = 8;
    const LANGUAGES = ['en-US', 'en'];
    const PLATFORM = 'Linux x86_64';  // must match the main thread / real UA OS

    // hardwareConcurrency is intentionally NOT patched here — see hardware_profile.js:
    // the real core count flows to both contexts, so they stay consistent even when
    // this (fragile) worker interception can't reach a given worker.
    const navigatorPatch = `
        Object.defineProperty(navigator, 'webdriver', {get: () => false, configurable: true});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => ${DEVICE_MEMORY}, configurable: true});
        Object.defineProperty(navigator, 'languages', {get: () => ${JSON.stringify(LANGUAGES)}, configurable: true});
        Object.defineProperty(navigator, 'platform', {get: () => '${PLATFORM}', configurable: true});
    `;

    // Patch Worker
    if (typeof Worker !== 'undefined') {
        const OriginalWorker = Worker;
        const workerHandler = {
            construct(target, args) {
                const [url, options] = args;
                try {
                    // For blob/data URLs or same-origin scripts, we can intercept
                    if (typeof url === 'string' && (url.startsWith('blob:') || url.startsWith('data:'))) {
                        return new OriginalWorker(url, options);
                    }
                    // For regular URLs, fetch the script, prepend our patch, create a blob
                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', url, false); // sync
                    xhr.send();
                    if (xhr.status === 200) {
                        const patched = navigatorPatch + '\n' + xhr.responseText;
                        const blob = new Blob([patched], {type: 'application/javascript'});
                        const blobUrl = URL.createObjectURL(blob);
                        const worker = new OriginalWorker(blobUrl, options);
                        // Clean up blob URL after a delay
                        setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
                        return worker;
                    }
                } catch(e) {
                    // Fall through to unpatched worker on error
                }
                return new OriginalWorker(url, options);
            }
        };
        window.Worker = new Proxy(OriginalWorker, workerHandler);
        // Preserve constructor appearance
        Object.defineProperty(window.Worker, 'name', {value: 'Worker'});
        Object.defineProperty(window.Worker, 'length', {value: 1});
    }

    // Patch SharedWorker similarly
    if (typeof SharedWorker !== 'undefined') {
        const OriginalSharedWorker = SharedWorker;
        const sharedHandler = {
            construct(target, args) {
                // SharedWorkers are harder to intercept since they may already be running
                // Just pass through - most detection sites don't test SharedWorker context
                return new OriginalSharedWorker(...args);
            }
        };
        window.SharedWorker = new Proxy(OriginalSharedWorker, sharedHandler);
    }
})();
