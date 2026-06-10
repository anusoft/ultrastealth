// AudioContext Fingerprint Noise
// Adds imperceptible noise to OfflineAudioContext output to prevent
// deterministic audio fingerprinting.

(function() {
    if (typeof OfflineAudioContext === 'undefined') return;

    // __usMask wraps each native method in a Proxy so toString/name stay native.
    const origStartRendering = OfflineAudioContext.prototype.startRendering;
    OfflineAudioContext.prototype.startRendering = __usMask(origStartRendering, function(target, thisArg, args) {
        return Reflect.apply(target, thisArg, args).then(function(buffer) {
            try {
                const data = buffer.getChannelData(0);
                // Add very small noise (inaudible, but changes the hash)
                for (let i = 0; i < data.length; i += 100) {
                    data[i] += (Math.random() - 0.5) * 0.00001;
                }
            } catch(e) {}
            return buffer;
        });
    });

    // Also patch AudioContext.createAnalyser to add noise to frequency data
    if (typeof AudioContext !== 'undefined') {
        const origCreateAnalyser = AudioContext.prototype.createAnalyser;
        AudioContext.prototype.createAnalyser = __usMask(origCreateAnalyser, function(target, thisArg, args) {
            const analyser = Reflect.apply(target, thisArg, args);
            const origGetFloatFreq = analyser.getFloatFrequencyData;
            analyser.getFloatFrequencyData = __usMask(origGetFloatFreq, function(t2, ta2, a2) {
                Reflect.apply(t2, ta2, a2);
                const array = a2[0];
                for (let i = 0; i < array.length; i += 50) {
                    array[i] += (Math.random() - 0.5) * 0.001;
                }
            });
            return analyser;
        });
    }
})();
