// Hardware Profile Spoofing
// Presents a consumer desktop, NOT a server. Values must stay consistent with the
// real browser's UA/OS: the system Chrome here runs on Linux (UA "X11; Linux x86_64",
// sec-ch-ua-platform "Linux"), so navigator.platform is "Linux x86_64". Claiming
// "Win32" against a Linux UA is exactly the inconsistency deviceandbrowserinfo /
// fingerprint-scan flag as a bot (isBot=true, high bot_risk_score).

(function() {
    // hardwareConcurrency is deliberately NOT overridden. The worker-consistency patch
    // can't reliably reach module/cross-origin workers, so a fixed main-thread value
    // (e.g. 8) leaks as a mismatch against the worker's real core count — the exact
    // hasInconsistentWorkerValues flag. Letting the real value flow to BOTH contexts
    // keeps them consistent. (16 cores is unremarkable for a consumer desktop.)

    // Define on Navigator.PROTOTYPE, never the navigator instance: instance-level
    // defineProperty makes Object.getOwnPropertyNames(navigator) non-empty, which real
    // Chrome never is — bot-detector.rebrowser.net checks exactly this.
    const NP = Navigator.prototype;

    // Device memory: 8GB — the spec-capped value real Chrome reports.
    if ('deviceMemory' in NP) {
        Object.defineProperty(NP, 'deviceMemory', {
            get: () => 8,
            configurable: true
        });
    }

    // Platform: must match the real UA's OS (Linux here) to stay consistent.
    Object.defineProperty(NP, 'platform', {
        get: () => 'Linux x86_64',
        configurable: true
    });

    // Max touch points: 0 (desktop, no touchscreen)
    Object.defineProperty(NP, 'maxTouchPoints', {
        get: () => 0,
        configurable: true
    });

    // Connection info (typical broadband)
    if ('connection' in navigator) {
        const conn = navigator.connection;
        if (conn) {
            try {
                Object.defineProperty(conn, 'effectiveType', {get: () => '4g', configurable: true});
                Object.defineProperty(conn, 'downlink', {get: () => 10, configurable: true});
                Object.defineProperty(conn, 'rtt', {get: () => 50, configurable: true});
            } catch(e) {}
        }
    }

    // Battery: desktop usually shows 100% charging. Patch on the prototype (not the
    // instance) and mask via Proxy so getBattery still reads as native.
    if ('getBattery' in NP && typeof __usMask !== 'undefined') {
        const origGetBattery = NP.getBattery;
        NP.getBattery = __usMask(origGetBattery, function(target, thisArg, args) {
            return Reflect.apply(target, thisArg, args).then(battery => {
                try {
                    Object.defineProperty(battery, 'charging', {get: () => true});
                    Object.defineProperty(battery, 'level', {get: () => 1.0});
                    Object.defineProperty(battery, 'chargingTime', {get: () => 0});
                    Object.defineProperty(battery, 'dischargingTime', {get: () => Infinity});
                } catch(e) {}
                return battery;
            });
        });
    }
})();
