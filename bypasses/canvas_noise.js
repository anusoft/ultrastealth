// Canvas Fingerprint Noise Injection
// Adds imperceptible, deterministic noise to canvas operations.
// Uses a seeded PRNG so multiple reads of the same canvas produce identical hashes.

(function() {
    // Simple seeded PRNG (Mulberry32)
    let seed = (Date.now() % 1000000) + Math.floor(Math.random() * 1000);
    function mulberry32() {
        seed |= 0;
        seed = seed + 0x6D2B79F5 | 0;
        let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
        t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
        return ((t ^ t >>> 14) >>> 0) / 4294967296;
    }

    // Patch toDataURL. __usMask wraps the native method in a Proxy so toString/name
    // stay native — the old plain-function replacement was caught by toString probes.
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = __usMask(origToDataURL, function(target, thisArg, args) {
        const ctx = thisArg.getContext('2d');
        if (ctx) {
            try {
                const imageData = ctx.getImageData(0, 0, thisArg.width, thisArg.height);
                const data = imageData.data;
                // Apply noise to ~20 random pixels
                const localSeed = seed; // Save seed state
                for (let i = 0; i < 20; i++) {
                    const idx = Math.floor(mulberry32() * (data.length / 4)) * 4;
                    const channel = Math.floor(mulberry32() * 3); // R, G, or B (not alpha)
                    const delta = mulberry32() > 0.5 ? 1 : -1;
                    data[idx + channel] = Math.max(0, Math.min(255, data[idx + channel] + delta));
                }
                ctx.putImageData(imageData, 0, 0);
                seed = localSeed; // Reset seed so subsequent calls produce same noise
            } catch(e) {
                // SecurityError on tainted canvases - ignore
            }
        }
        return Reflect.apply(target, thisArg, args);
    });

    // Patch toBlob
    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = __usMask(origToBlob, function(target, thisArg, args) {
        // Trigger noise via toDataURL path first
        thisArg.toDataURL();
        return Reflect.apply(target, thisArg, args);
    });

    // Patch getImageData to add consistent noise
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = __usMask(origGetImageData, function(target, thisArg, args) {
        const imageData = Reflect.apply(target, thisArg, args);
        const data = imageData.data;
        const localSeed = seed;
        for (let i = 0; i < 10; i++) {
            const idx = Math.floor(mulberry32() * (data.length / 4)) * 4;
            const channel = Math.floor(mulberry32() * 3);
            const delta = mulberry32() > 0.5 ? 1 : -1;
            data[idx + channel] = Math.max(0, Math.min(255, data[idx + channel] + delta));
        }
        seed = localSeed;
        return imageData;
    });
})();
